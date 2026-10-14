# SSH access

Use the operator key at `~/.ssh/asius_operator_ed25519`, or set `DRAGON_SSH_KEY`.
Add its public key through the app or local USB/UART access.
Fresh devices allow `comma/comma` over USB NCM and UART. Remote SSH requires individual keys.
There is no shared bootstrap SSH key. Host tools retain known host keys.
After a factory reset changes the host identity, verify it locally before replacing the saved host key.
