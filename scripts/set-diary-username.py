"""Pin an explicitly verified diary username without renaming the archive.

Run inside the app container: python - USERNAME < scripts/set-diary-username.py
No credentials or personal data are printed. The hint is principal-bound.
"""
import hashlib
import json
import sys
from myfitnesspal_mcp import auth, config, mfp_client

username = sys.argv[1] if len(sys.argv) == 2 else ""
if not username or any(c in username for c in "@/?#\\"):
    raise SystemExit("Supply a MyFitnessPal username, not an email or URL.")
client = mfp_client.get_client()
account = auth.saved_username()
principal = hashlib.sha256(str(client.user_id).encode()).hexdigest()
directory = config.account_data_dir(account)
if (directory / 'principal.sha256').read_text().strip() != principal:
    raise SystemExit("Account identity mismatch; no changes made.")
auth._atomic_write(directory / 'diary-username.json', json.dumps({
    'username': username, 'principal_sha256': principal}) + '\n')
print('Principal-bound diary route saved; archive and credentials unchanged.')
