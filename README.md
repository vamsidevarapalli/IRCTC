# Train Ticket Assistant

A command-line IRCTC preparation assistant with supervised form filling.
It never submits a booking or payment, enters credentials, or handles CAPTCHA/OTP.
Use browser automation only where permitted by IRCTC and your device administrator.

## Run on this machine

```powershell
& 'C:/Program Files/Python313/python.exe' train_ticket_assistant.py
```

For another Python environment, install `requirements.txt` with pip first.
Browser filling requires Google Chrome and permission to use browser automation.
The preparation, export and PNR features use the Python standard library.

## Booking From a Config File

Edit `booking_config.toml` with your stations, boarding date, optional train number,
class, quota (`GENERAL` or `TATKAL`), and passenger details. Repeat `[[passengers]]`
for each person. For Tatkal, provide the train's departure date at its originating station.
Keep dates and train numbers in quotes; age is an unquoted integer.
The blank template intentionally fails validation until filled in.

Validate without opening a browser:

```powershell
& './.venv/Scripts/python.exe' train_ticket_assistant.py --config booking_config.toml --check-config
```

Start supervised filling using those details, without selecting saved journeys/passengers:

```powershell
& './.venv/Scripts/python.exe' train_ticket_assistant.py --config booking_config.toml
```

Menu option 12 uses the same file. Relative command-line paths resolve from the current
directory; option 12 uses the file beside the script. Config execution does not modify
`assistant_data.json`. Config files contain plaintext personal information: protect them
and never add login passwords, OTPs or payment secrets. Unknown fields are rejected.
Python 3.11 or newer is required for TOML support.

This is not unattended ticket booking or a timed booking scheduler. Login, CAPTCHA/OTP,
train selection, confirmation and payment remain manual. The administrator-policy blocker
below still applies. Browser filling also requires Playwright in the environment you run.

## Workflow

1. Add passengers (option 1) and journeys (option 4).
2. Use option 7 to review a booking brief, optionally export it, and open IRCTC normally.
3. Use option 10 for assisted form filling. In its separate Chrome window, dismiss notices and log in yourself, then return to the terminal checkpoint.
4. After search fields are filled, review them and click Search yourself. Select the train, class and quota, and open Passenger Details manually.
5. Add the requested number of passenger rows, then return to the terminal to fill them. Review every field, including berth preferences, before continuing yourself.
6. Complete contact details, CAPTCHA/OTP, review and payment manually. Leave the terminal checkpoint waiting until finished; pressing Enter at the final checkpoint closes that browser session.

Use exact station codes for reliable autocomplete matching. Ambiguous matches stop filling.
Unsupported passenger form layouts stop filling; earlier fields may already be filled.
No login cookies or credentials are persisted by this assistant.

## Tatkal Reminders

Option 11 exports an `.ics` event with an alarm five minutes before booking opens.
Import it into your calendar and enable calendar notifications; the script is not a background scheduler.
Timing uses the train's departure date at its originating station, not necessarily your boarding date.
Confirm that date against the timetable. Standard opening times are 10:00 IST for AC and
11:00 IST for non-AC, on the preceding day; verify current eligibility/rules on IRCTC.
Tatkal 1A/EC is rejected. Maximum supported passenger counts are four for Tatkal and six otherwise.
Infant handling and special concessions remain manual on IRCTC.

## PNR Lookup

Option 8 requires your own authorized RapidAPI-compatible JSON endpoint configured locally:

- `PNR_API_URL`: HTTPS URL containing `{pnr}` in its path or query.
- `PNR_API_KEY`: your key, sent as `x-rapidapi-key`.
- `PNR_API_HOST`: optional `x-rapidapi-host` header.

Configure the secret locally, never in chat or source files. Other authentication schemes are not supported.
Redirects are refused to avoid forwarding the API key to a different endpoint.
The provider's JSON response is displayed without assuming a particular response schema.

## Data and Limitations

`assistant_data.json` stores passenger details in plaintext. Optional booking briefs under
`exports/` also contain personal information. Protect these files and delete exports when no longer needed.
Saves use atomic replacement. Malformed saved data stops startup without overwriting the file.
Legacy records remain viewable/removable; invalid details are rejected when preparing a booking.
Existing journeys without a train-origin date remain readable; Tatkal preparation requests that date.

On this machine, Edge reported that administrator policy disables remote debugging and
headless mode. The assistant now uses Google Chrome: a standard visible automation launch
succeeded without changing browser policies. If Chrome is blocked in your environment,
use option 7 and ask IT for approval. Do not bypass enterprise policies.

Search selectors were inspected on the public IRCTC page. The authenticated passenger form
and full booking workflow have not been verified live. IRCTC UI changes may require selector updates.
PNR requests are tested with mocked responses; no authorized live API was configured for verification.

## Tests

```powershell
& 'C:/Program Files/Python313/python.exe' -m unittest discover -v
```

Tests cover validation, Tatkal timing, exports, atomic saves, station matching, mocked browser
filling and mocked PNR responses. They do not log in, book tickets, or make payments.