"""Sandbox secrets: HMAC key for account number hashes, MFA seeds, full synthetic numbers.

Everything lives under ``secrets/`` (gitignored) and is generated on first use. The values are
registered with the prompt guard (``register_sandbox_secrets``) so they can never appear in any
prompt (INV-12), even though all of them are synthetic.
"""

import hashlib
import hmac
import json
import os
import secrets as _secrets
from base64 import b32encode
from pathlib import Path

from corridor.config import REPO_ROOT
from corridor.llm.guard import SecretRegistry

SECRETS_DIR = REPO_ROOT / "secrets"
HMAC_KEY_FILE = "sandbox_hmac.key"
ACCOUNTS_FILE = "sandbox_accounts.json"
MFA_DIR = "mfa"
PASSPORT_KEY_FILE = "passport_issuer_ed25519.key"
PASSPORT_HOLDER_DIR = "passport_holder"


def _dir(secrets_dir: Path | None) -> Path:
    return secrets_dir or SECRETS_DIR


def _write_new_secret(path: Path, value: str) -> None:
    """Create ``path`` atomically with mode 0600 (never world readable, never overwritten).

    If another process created it first the existing value wins, so concurrent starts agree."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)


def hmac_key(secrets_dir: Path | None = None) -> bytes:
    """Return the HMAC key, creating a random one the first time."""
    path = _dir(secrets_dir) / HMAC_KEY_FILE
    if not path.exists():
        _write_new_secret(path, _secrets.token_hex(32))
    return path.read_text(encoding="utf-8").strip().encode()


def passport_signing_key_hex(secrets_dir: Path | None = None) -> str:
    """The Credit Passport issuer's Ed25519 private key (32 bytes, hex), created the first time.

    A synthetic key for the sandbox issuer ("Corridor AI sandbox bank"). It never appears in a
    prompt, log or response (INV-12): the prompt guard knows it like every other secret."""
    path = _dir(secrets_dir) / PASSPORT_KEY_FILE
    if not path.exists():
        _write_new_secret(path, _secrets.token_hex(32))
    return path.read_text(encoding="utf-8").strip()


_GUARDS: list[SecretRegistry] = []


def watch_new_secrets(registry: SecretRegistry) -> None:
    """Registries that must learn a secret the moment it is created (not only at start)."""
    if registry not in _GUARDS:
        _GUARDS.append(registry)


def passport_holder_key_hex(user_id: str, secrets_dir: Path | None = None) -> str:
    """A user's Credit Passport holder key (Ed25519 private key, 32 bytes hex), created on
    first use.

    In a real product this key lives on the customer's device and never leaves it. The sandbox
    has no
    device, so it sits next to the other synthetic secrets; it is never sent to a model or
    returned by
    any route (INV-12)."""
    path = _dir(secrets_dir) / PASSPORT_HOLDER_DIR / f"{user_id}.key"
    if not path.exists():
        _write_new_secret(path, _secrets.token_hex(32))
    key = path.read_text(encoding="utf-8").strip()
    for (
        registry
    ) in _GUARDS:  # a key created after start is known to the prompt guard at once (INV-12)
        registry.register(key)
    return key


def account_hash(number: str, secrets_dir: Path | None = None) -> str:
    """HMAC-SHA256 of an account number, hex. Only this is stored in the database."""
    return hmac.new(hmac_key(secrets_dir), number.encode(), hashlib.sha256).hexdigest()


def mfa_seed(user_id: str, secrets_dir: Path | None = None) -> str:
    """Return the TOTP seed (base32) for a user, creating it the first time."""
    path = _dir(secrets_dir) / MFA_DIR / f"{user_id}.seed"
    if not path.exists():
        _write_new_secret(path, b32encode(_secrets.token_bytes(10)).decode())
    return path.read_text(encoding="utf-8").strip()


def mfa_seed_ref(user_id: str) -> str:
    """The reference stored in users.mfa_seed_ref (a relative path, never the seed)."""
    return f"secrets/{MFA_DIR}/{user_id}.seed"


DEMO_TOKENS_FILE = "demo_tokens.json"
OFFICER_TOKENS_FILE = "officer_tokens.json"
OFFICER_IDS = (
    "officer_asha",
    "officer_binod",
)  # synthetic bank officers, each with their own credential
OFFICER_ID = OFFICER_IDS[0]
DEMO_USERS = ("usr_priya", "usr_ramesh", "usr_anil")


def demo_tokens(secrets_dir: Path | None = None) -> dict[str, str]:
    """Fixed demo bearer tokens, one per persona (user id -> token), created once under secrets/.

    Demo authentication only: the authenticated user id comes from the token and nowhere else."""
    path = _dir(secrets_dir) / DEMO_TOKENS_FILE
    if not path.exists():
        tokens = {u: "demo_" + _secrets.token_urlsafe(24) for u in DEMO_USERS}
        _write_new_secret(path, json.dumps(tokens, indent=2))
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in loaded.items()}


def officer_tokens(secrets_dir: Path | None = None) -> dict[str, str]:
    """One credential per synthetic bank officer (officer id -> token), created once under
    secrets/ (demo only).

    Each officer logs in with their own credential and every officer action is ledgered under
    that officer's
    id. A customer token never equals any of them: they are separate secrets (ADR 0012, 0019)."""
    path = _dir(secrets_dir) / OFFICER_TOKENS_FILE
    if not path.exists():
        tokens = {o: "officer_" + _secrets.token_urlsafe(24) for o in OFFICER_IDS}
        _write_new_secret(path, json.dumps(tokens, indent=2))
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in loaded.items()}


def officer_token(secrets_dir: Path | None = None) -> str:
    """The first officer's credential (kept for scripts that need one)."""
    return officer_tokens(secrets_dir)[OFFICER_ID]


def write_accounts_file(numbers: dict[str, str], secrets_dir: Path | None = None) -> None:
    """Write the full synthetic account numbers (key -> number) to secrets/sandbox_accounts.json."""
    path = _dir(secrets_dir) / ACCOUNTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(numbers, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


def register_sandbox_secrets(registry: SecretRegistry, secrets_dir: Path | None = None) -> int:
    """Register the HMAC key, every MFA seed and every full account number. Returns the count."""
    base = _dir(secrets_dir)
    watch_new_secrets(registry)
    count = 0
    key_file = base / HMAC_KEY_FILE
    if key_file.exists():
        registry.register(key_file.read_text(encoding="utf-8").strip())
        count += 1
    for seed_file in sorted((base / MFA_DIR).glob("*.seed")) if (base / MFA_DIR).exists() else []:
        registry.register(seed_file.read_text(encoding="utf-8").strip())
        count += 1
    # the Credit Passport issuer key exists from the first start, so it is always known to the guard
    registry.register(passport_signing_key_hex(base))
    count += 1
    for (
        demo_user
    ) in DEMO_USERS:  # holder keys exist from the first start, so the guard always knows them
        passport_holder_key_hex(demo_user, base)
    holders = base / PASSPORT_HOLDER_DIR
    for key_file in sorted(holders.glob("*.key")) if holders.exists() else []:
        registry.register(key_file.read_text(encoding="utf-8").strip())
        count += 1
    tokens = base / DEMO_TOKENS_FILE
    if tokens.exists():
        for token in json.loads(tokens.read_text(encoding="utf-8")).values():
            registry.register(str(token))
            count += 1
    officers = base / OFFICER_TOKENS_FILE
    if officers.exists():
        for token in json.loads(officers.read_text(encoding="utf-8")).values():
            registry.register(str(token))
            count += 1
    accounts = base / ACCOUNTS_FILE
    if accounts.exists():
        for number in json.loads(accounts.read_text(encoding="utf-8")).values():
            registry.register(str(number))
            count += 1
    return count
