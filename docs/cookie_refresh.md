# Refreshing the controller session cookie

`unifi-skill` authenticates with the same browser session cookie the
controller's own web UI uses. Controller sessions expire (typically within a
day of inactivity), so expect exit code 10 occasionally. The tool never
stores or transmits your controller password; a human exports the cookie.

## Procedure

1. Open the controller UI in a browser and log in
   (e.g. `https://<your-controller-ip>:<port>/`).
2. Get the two cookie values (`TOKEN` and `JSESSIONID`) from the browser:

   **DevTools (any browser):** open DevTools → Application (or Storage) →
   Cookies → select the controller origin → copy the values of `TOKEN` and
   `JSESSIONID`.

3. Write them, joined, into your cookie file (the path you set as
   `UNIFI_COOKIE_FILE`; default `~/.config/unifi-skill/cookie.txt`):

   ```bash
   mkdir -p ~/.config/unifi-skill
   cat > ~/.config/unifi-skill/cookie.txt
   # paste:  TOKEN=<value>; JSESSIONID=<value>   then Ctrl-D
   chmod 600 ~/.config/unifi-skill/cookie.txt
   ```

   A line pasted verbatim from DevTools' `Cookie:` request header also works;
   the loader strips the leading `Cookie:` prefix.

4. Verify: `unifi-skill aps --json` should exit 0.

## Notes

- Keep the cookie file outside any git repository; this repo's `.gitignore`
  also blocks `*cookie*.txt` as a second guard.
- Logging out of the controller UI, or the controller restarting,
  invalidates the cookie.
- For automation-flavored setups: a Playwright/CDP browser session that is
  already logged in can export the cookies programmatically
  (`context.cookies()` filtered to the controller origin) — the same values,
  just without the manual copy. The login itself still needs a human.
- Session lifetime is controlled by the controller's settings
  (`Settings → Admin → Session Timeout` on some versions); lengthening it
  trades against an unattended cookie being usable longer if leaked.
