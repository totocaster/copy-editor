# Security

Copy Editor is intended for one person on a trusted computer. Its server
binds to `127.0.0.1`; it has no user accounts or authentication for remote
users. Requests are restricted to the loopback client and local host, and
writes require a per-process token. Do not expose it through port forwarding,
a reverse proxy, or a public host. Software on the same computer may still be
able to reach the local app and view stored writing.

The SQLite database, backups, exported text, and browser local storage drafts can
contain private writing. Keep them in private locations, protect your user
account, and avoid sharing logs or test fixtures containing real content.
Editing passes transmit prompt context to the selected signed-in model
provider; review the Data, backups, and access section of the README and
the Settings → Account privacy option before running a pass.

To report a vulnerability, use the repository's **Report a vulnerability**
link under its GitHub Security tab if available. If private reporting is not
enabled, open a brief public issue asking the maintainer for a private contact
channel. Do not put exploit steps, private documents, credentials, or database
copies in that issue. Include affected version, impact, reproduction steps,
and a suggested fix in the private report. Security fixes should be tested
with invented data and without a live model account.
