"""Security policies — pure-Python rules, fully unit-testable without a
docker daemon.

Three layers:

1. Command classification (dangerous-command detection). The doc's
   default-deny / human-approval list is encoded as rules; compound
   commands (sh -c "…", pipelines) are scanned recursively.
2. Secret/env filtering. Secrets (SSH keys, cloud credentials, personal
   tokens, the host docker socket) NEVER pass into the sandbox — even
   an explicit allowlist entry cannot override the secret deny-list.
   Everything else needs an allowlist entry (default: minimal env).
3. Path traversal / repository-root restriction. A path is usable only
   if it resolves inside the allowed root — lexically for not-yet-
   existing files and through symlinks for existing ones.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

# ─── 1. dangerous-command detection ────────────────────────────

# Default-deny rules. Each rule is (name, predicate) over the full token
# list of a command; matches are fatal regardless of where they appear.
DENY_RULES: dict[str, str] = {
    "rm_rf_root": "rm -rf on / (or /*) — deletes the root filesystem",
    "sudo": "sudo — privilege escalation",
    "curl_pipe_shell": "curl/wget piped into a shell — remote code execution",
    "ssh": "ssh — outbound shell to remote hosts",
    "scp": "scp — file exfiltration to remote hosts",
    "docker_privileged": "docker --privileged — container escape",
    "git_push_force": "git push --force — history overwrite",
}

# Human-approval rules: not fatal, but the runner requires an explicit
# confirmation callback to execute them.
CONFIRM_RULES: dict[str, str] = {
    "chmod_recursive": "chmod -R — recursive permission changes",
    "docker": "docker client usage (any form) — requires approval",
}

_SHELLS = ("sh", "bash", "zsh", "dash", "fish", "ksh")


def _has_flag(tokens: list[str], flag: str) -> bool:
    if flag in tokens:
        return True
    short = flag.lstrip("-")
    for tok in tokens:
        if tok.startswith("-") and not tok.startswith("--") and short in tok[1:]:
            return True
    return False


@dataclass(frozen=True)
class CommandVerdict:
    action: str          # "allow" | "deny" | "confirm"
    rule: str = ""       # matched rule name ("" for allow)
    reason: str = ""     # human-readable reason


def _is_shell(tok: str) -> bool:
    base = tok.split("/")[-1]
    return base in _SHELLS


def classify_command(command: str) -> CommandVerdict:
    """Classify a shell command string. Returns the most severe verdict:
    deny > confirm > allow. Inner commands (sh -c "…", curl|bash
    pipelines, `&&`/`;` chains) are scanned recursively."""
    if not command or not command.strip():
        return CommandVerdict("allow")
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unparseable command → deny by default (fail closed).
        return CommandVerdict("deny", "unparseable", "command cannot be parsed safely")
    if not tokens:
        return CommandVerdict("allow")
    return _classify_tokens(tokens)


def _classify_tokens(tokens: list[str]) -> CommandVerdict:
    verdict = CommandVerdict("allow")

    # 1) Default-deny list — evaluated first so the OUTER command's rule
    #    wins when several rules match at equal severity.
    if _has_rm_rf_root(tokens):
        verdict = _worse(verdict, CommandVerdict("deny", "rm_rf_root", DENY_RULES["rm_rf_root"]))
    if "sudo" in tokens:
        verdict = _worse(verdict, CommandVerdict("deny", "sudo", DENY_RULES["sudo"]))
    if _pipe_into_shell(tokens):
        verdict = _worse(verdict, CommandVerdict(
            "deny", "curl_pipe_shell", DENY_RULES["curl_pipe_shell"]))
    if _leading_cmd(tokens, "ssh"):
        verdict = _worse(verdict, CommandVerdict("deny", "ssh", DENY_RULES["ssh"]))
    if _leading_cmd(tokens, "scp"):
        verdict = _worse(verdict, CommandVerdict("deny", "scp", DENY_RULES["scp"]))
    if "--privileged" in tokens:
        verdict = _worse(verdict, CommandVerdict(
            "deny", "docker_privileged", DENY_RULES["docker_privileged"]))
    if _git_push_force(tokens):
        verdict = _worse(verdict, CommandVerdict(
            "deny", "git_push_force", DENY_RULES["git_push_force"]))

    # 2) Recursive scan: inner command strings (sh -c "…") and compound
    #    commands (pipelines, && / ; chains) are classified segment-wise —
    #    a dangerous payload cannot hide inside a compound command.
    for i, tok in enumerate(tokens):
        if tok == "-c" and i >= 1 and i + 1 < len(tokens) and _is_shell(tokens[i - 1]):
            verdict = _worse(verdict, classify_command(tokens[i + 1]))
    segments = _split_segments(tokens)
    if len(segments) > 1:
        for seg in segments:
            verdict = _worse(verdict, _classify_tokens(seg))

    # 3) Human-approval list.
    if _leading_cmd(tokens, "chmod") and _has_flag(tokens, "R"):
        verdict = _worse(verdict, CommandVerdict(
            "confirm", "chmod_recursive", CONFIRM_RULES["chmod_recursive"]))
    if _leading_cmd(tokens, "docker"):
        verdict = _worse(verdict, CommandVerdict(
            "confirm", "docker", CONFIRM_RULES["docker"]))

    return verdict


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """Split a token list on top-level |, &&, ; into segments."""
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in ("|", "&&", "||", ";"):
            if current:
                segments.append(current)
                current = []
            segments.append([tok])
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments if len(segments) > 1 else []


def _pipe_into_shell(tokens: list[str]) -> bool:
    """`<anything> | sh` — piping into a shell executes remote content."""
    for i, tok in enumerate(tokens):
        if tok == "|" and i + 1 < len(tokens) and _is_shell(tokens[i + 1]):
            return True
    return False


def _has_rm_rf_root(tokens: list[str]) -> bool:
    """rm -rf / (or /*) — any rm with -r and -f whose target is the root."""
    for i, tok in enumerate(tokens):
        if tok == "rm" or tok.endswith("/rm"):
            rest = tokens[i + 1:]
            if _has_flag(rest, "r") and _has_flag(rest, "f"):
                targets = [t for t in rest if not t.startswith("-")]
                if any(t == "/" or t.startswith("/*") for t in targets):
                    return True
    return False


def _leading_cmd(tokens: list[str], cmd: str) -> bool:
    return bool(tokens) and tokens[0].split("/")[-1] == cmd


def _git_push_force(tokens: list[str]) -> bool:
    """git push with force flags (--force / --force-with-lease / -f) or a
    +refspec."""
    if not (_leading_cmd(tokens, "git") and "push" in tokens[1:]):
        return False
    rest = tokens[tokens.index("push") + 1:]
    if any(t in ("--force", "--force-with-lease") for t in rest):
        return True
    if _has_flag(rest, "f"):
        return True
    return any(t.startswith("+") and ":" in t for t in rest)


def _worse(a: CommandVerdict, b: CommandVerdict) -> CommandVerdict:
    order = {"allow": 0, "confirm": 1, "deny": 2}
    return b if order[b.action] > order[a.action] else a


# ─── 2. secret / environment filtering ─────────────────────────

# Environment variables that must NEVER be injected into the sandbox,
# matched case-insensitively by regex — SSH keys and agent sockets, cloud
# credentials, personal tokens, and the host docker socket.
SECRET_ENV_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^SSH_",                      # SSH_AUTH_SOCK, SSH_AGENT_PID, SSH_* keys
    r"^(AWS|GCP|GOOGLE|AZURE|ALIYUN|TENCENT|CLOUDFLARE|DO)_",  # cloud credentials
    r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY)$",
    r"(TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY|PRIVATE_KEY)_",
    r"_TOKEN$", r"_SECRET$", r"_PASSWORD$", r"_CREDENTIALS?$", r"_API_KEY$",
    r"^DOCKER_HOST$",              # host docker socket (unix:///var/run/docker.sock)
    r"^KUBECONFIG$", r"^NPMRC$", r"^PIP_INDEX_URL$", r"^GIT_ASKPASS$",
    r"^HOMEBREW_GITHUB_API_TOKEN$",
))

# The minimal env a sandbox command may legitimately need. Anything else
# must be allowlisted explicitly by the policy.
DEFAULT_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "PYTHONUNBUFFERED", "PYTHONIOENCODING",
})


class SecretFilter:
    """Never-inject rules + strict allowlist for sandbox environments."""

    def __init__(self, allowlist: frozenset[str] | set[str] = DEFAULT_ENV_ALLOWLIST,
                 patterns: tuple[re.Pattern, ...] = SECRET_ENV_PATTERNS):
        self.allowlist = set(allowlist)
        self.patterns = patterns

    def is_secret(self, name: str) -> bool:
        # search, not match: prefix patterns anchor at ^, suffix patterns
        # anchor at $ — both need to be found anywhere in the name.
        return any(p.search(name) for p in self.patterns)

    def filter_env(self, env: dict[str, str]) -> dict[str, str]:
        """Secret deny-list wins over everything; the allowlist gates the
        rest. Returns the env that MAY be passed into the sandbox."""
        out = {}
        for name, value in env.items():
            if self.is_secret(name):
                continue  # never injected — not even via the allowlist
            if name in self.allowlist:
                out[name] = value
        return out


# ─── 3. path traversal / repository-root restriction ───────────

class PathTraversalGuard:
    """A path is allowed only if it resolves inside the restricted root.

    Lexical resolution covers files that don't exist yet (write paths);
    strict resolution follows symlinks so an existing symlink pointing
    outside the root is rejected."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def check(self, path: str | Path, *, strict: bool = True) -> CommandVerdict:
        """strict=True follows symlinks (for existing paths); paths that
        don't exist yet fall back to lexical resolution (write paths)."""
        try:
            p = Path(path)
            if "\x00" in str(path):
                return CommandVerdict("deny", "nul_byte", "path contains a NUL byte")
            if p.is_absolute():
                candidate = p
            else:
                candidate = self.root / p
            try:
                resolved = candidate.resolve(strict=strict)
            except FileNotFoundError:
                resolved = candidate.resolve(strict=False)  # not created yet
            inside = resolved == self.root or self.root in resolved.parents
            if inside:
                return CommandVerdict("allow")
            return CommandVerdict(
                "deny", "path_traversal",
                f"{path!r} resolves outside the restricted root {self.root}")
        except (OSError, ValueError) as e:
            return CommandVerdict("deny", "path_error", f"invalid path {path!r}: {e}")


def resolve_inside(root: str | Path, path: str | Path) -> Path:
    """Resolve a relative path against the root, denying traversal. Raises
    PermissionError on escape — the imperative form of PathTraversalGuard."""
    verdict = PathTraversalGuard(root).check(path)
    if verdict.action != "allow":
        raise PermissionError(verdict.reason)
    p = Path(path)
    return (Path(root) / p).resolve() if not p.is_absolute() else p.resolve()
