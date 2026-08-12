# Clipboard pack/unpack sync (optionda)

## Goal

Quick multi-machine sync via a pasteable code (no sync files). Receiver unpacks, replaces the account, restores config + obfuscated Alpaca keys, then auto `refresh-iv`.

## Payload

Included: active account snapshot, `config.toml` fields, Alpaca credentials (obfuscated).  
Excluded: journal JSONL, IV surface JSON (rebuilt by `refresh-iv`).

## Format

- Code: `oda1.` + urlsafe-base64(zlib(JSON))
- Fingerprint: `sha256:` + hex digest of the full code string
- Creds: XOR with SHA256(`optionda-pack-v1` + salt); not real encryption

## CLI

- `optionda pack` — print code + sha256
- `optionda unpack <code> [--sha256 HEX] [--yes] [--no-refresh]`
- Existing same-name account requires `--yes` or interactive confirm
- Default: overwrite account, write config/creds, activate, `refresh-iv`

## Import policy

Full replace of the named account. If pack has no creds, leave local credentials unchanged.
