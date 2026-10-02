"""Train Ticket Booking Assistant (IRCTC helper).

A personal-productivity helper with supervised browser form filling.
Login, CAPTCHA, OTP, booking confirmation and payment remain manual.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_FILE = Path(__file__).with_name("assistant_data.json")
CONFIG_FILE = Path(__file__).with_name("booking_config.toml")
IRCTC_URL = "https://www.irctc.co.in/nget/train-search"
IST = timezone(timedelta(hours=5, minutes=30), "IST")

# PNR lookup uses YOUR OWN authorized API. Set these env vars before use:
#   PNR_API_KEY   - your API key
#   PNR_API_HOST  - RapidAPI host, e.g. irctc1.p.rapidapi.com
#   PNR_API_URL   - full endpoint; {pnr} is replaced with the PNR number
PNR_API_KEY_ENV = "PNR_API_KEY"
PNR_API_HOST_ENV = "PNR_API_HOST"
PNR_API_URL_ENV = "PNR_API_URL"

BERTH_CHOICES = ["No preference", "Lower", "Middle", "Upper", "Side Lower", "Side Upper"]
CLASS_CHOICES = ["1A", "2A", "3A", "3E", "SL", "CC", "2S", "EC"]


@dataclass
class Passenger:
    name: str
    age: int
    gender: str
    berth: str = "No preference"


@dataclass
class Journey:
    origin: str
    destination: str
    date: str  # DD-MM-YYYY
    train_no: str = ""
    travel_class: str = "SL"
    quota: str = "GENERAL"  # or TATKAL
    train_origin_date: str = ""


@dataclass
class Store:
    passengers: list[Passenger] = field(default_factory=list)
    journeys: list[Journey] = field(default_factory=list)


def validate_passenger(passenger: Passenger) -> None:
    if not isinstance(passenger.name, str) or not passenger.name.strip():
        raise ValueError("Passenger name is required.")
    if type(passenger.age) is not int or not 1 <= passenger.age <= 125:
        raise ValueError("Passenger age must be between 1 and 125; handle infants on IRCTC.")
    if passenger.gender not in {"Male", "Female", "Transgender"}:
        raise ValueError("Invalid passenger gender.")
    if passenger.berth not in BERTH_CHOICES:
        raise ValueError("Invalid berth preference.")


def validate_journey(journey: Journey, *, allow_past: bool = False) -> None:
    if not journey.origin.strip() or not journey.destination.strip():
        raise ValueError("Both stations are required.")
    if journey.origin.strip().casefold() == journey.destination.strip().casefold():
        raise ValueError("Origin and destination must be different.")
    travel_date = datetime.strptime(journey.date, "%d-%m-%Y").date()
    if not allow_past and travel_date < datetime.now(IST).date():
        raise ValueError("Journey date cannot be in the past.")
    if journey.travel_class not in CLASS_CHOICES or journey.quota not in {"GENERAL", "TATKAL"}:
        raise ValueError("Invalid class or quota.")
    if journey.train_no and not (journey.train_no.isascii() and journey.train_no.isdigit() and len(journey.train_no) == 5):
        raise ValueError("Train number must contain five digits.")
    if journey.quota == "TATKAL" and journey.travel_class in {"1A", "EC"}:
        raise ValueError("Tatkal is not offered for 1A or EC.")
    if journey.train_origin_date:
        origin_date = datetime.strptime(journey.train_origin_date, "%d-%m-%Y").date()
        if origin_date > travel_date:
            raise ValueError("Train origin date cannot be after your boarding date.")


def tatkal_opening(journey: Journey) -> datetime:
    validate_journey(journey, allow_past=True)
    if journey.quota != "TATKAL":
        raise ValueError("Tatkal timing requires a TATKAL journey.")
    if not journey.train_origin_date:
        raise ValueError("Confirm the train's departure date at its originating station first.")
    origin_date = datetime.strptime(journey.train_origin_date, "%d-%m-%Y")
    opening_hour = 10 if journey.travel_class in {"2A", "3A", "3E", "CC"} else 11
    return (origin_date - timedelta(days=1)).replace(hour=opening_hour, tzinfo=IST)


def load_store() -> Store:
    if not DATA_FILE.exists():
        return Store()
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    store = Store(
        passengers=[Passenger(**p) for p in raw.get("passengers", [])],
        journeys=[Journey(**j) for j in raw.get("journeys", [])],
    )
    return store


def load_booking_config(path: Path) -> tuple[Journey, list[Passenger]]:
    with path.open("rb") as source:
        raw = tomllib.load(source)
    if set(raw) != {"journey", "passengers"}:
        raise ValueError("Config must contain only [journey] and [[passengers]] sections.")
    journey_data = raw["journey"]
    passenger_data = raw["passengers"]
    if not isinstance(journey_data, dict) or not all(isinstance(value, str) for value in journey_data.values()):
        raise ValueError("All journey fields must be quoted text, including the train number and dates.")
    if not isinstance(passenger_data, list) or not passenger_data:
        raise ValueError("Add at least one [[passengers]] section.")
    passengers = []
    for number, details in enumerate(passenger_data, 1):
        if not isinstance(details, dict):
            raise ValueError(f"Passenger {number} must be a [[passengers]] section.")
        if any(not isinstance(value, str) for key, value in details.items() if key != "age"):
            raise ValueError(f"Passenger {number}: all fields except age must be quoted text.")
        try:
            passenger = Passenger(**details)
        except TypeError as exc:
            raise ValueError(f"Passenger {number}: missing or unknown fields; use name, age, gender, berth.") from exc
        validate_passenger(passenger)
        passengers.append(passenger)
    try:
        journey = Journey(**journey_data)
    except TypeError as exc:
        raise ValueError("Journey has missing or unknown fields. Follow booking_config.toml.") from exc
    booking_brief(journey, passengers)
    return journey, passengers


def run_booking_config(path: Path, *, check_only: bool = False) -> None:
    journey, passengers = load_booking_config(path)
    if check_only:
        print(f"Config valid: {journey.quota}, {journey.travel_class}, {len(passengers)} passenger(s). No browser opened.")
        return
    print(booking_brief(journey, passengers))
    print("Assisted filling only: login, CAPTCHA/OTP, train selection, confirmation and payment remain manual.")
    from irctc_browser import assist_booking
    assist_booking(journey, passengers)


def save_store(store: Store) -> None:
    payload = {
        "passengers": [asdict(p) for p in store.passengers],
        "journeys": [asdict(j) for j in store.journeys],
    }
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DATA_FILE.parent,
                                         suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, DATA_FILE)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_choice(prompt: str, choices: list[str], default: str) -> str:
    print(f"{prompt} (choose one):")
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    raw = input(f"Number [{choices.index(default) + 1}]: ").strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(choices):
        return choices[int(raw) - 1]
    print("Invalid choice, using default.")
    return default


def add_passenger(store: Store) -> None:
    name = ask("Full name")
    if not name:
        print("Name is required.")
        return
    age_raw = ask("Age")
    if not age_raw.isdigit():
        print("Age must be a number.")
        return
    gender = ask_choice("Gender", ["Male", "Female", "Transgender"], "Male")
    berth = ask_choice("Berth preference", BERTH_CHOICES, "No preference")
    passenger = Passenger(name=name, age=int(age_raw), gender=gender, berth=berth)
    validate_passenger(passenger)
    store.passengers.append(passenger)
    save_store(store)
    print(f"Added passenger: {name}")


def add_journey(store: Store) -> None:
    origin = ask("From station (code or name)")
    destination = ask("To station (code or name)")
    date = ask("Journey date (DD-MM-YYYY)")
    try:
        datetime.strptime(date, "%d-%m-%Y")
    except ValueError:
        print("Date must be DD-MM-YYYY.")
        return
    train_no = ask("Train number (optional)")
    travel_class = ask_choice("Class", CLASS_CHOICES, "SL")
    quota = ask_choice("Quota", ["GENERAL", "TATKAL"], "GENERAL")
    journey = Journey(
            origin=origin,
            destination=destination,
            date=date,
            train_no=train_no,
            travel_class=travel_class,
            quota=quota,
        )
    if quota == "TATKAL":
        journey.train_origin_date = ask("Train departure date at its originating station (DD-MM-YYYY)", date)
    validate_journey(journey)
    store.journeys.append(journey)
    save_store(store)
    print(f"Added journey: {origin} -> {destination} on {date}")


def list_passengers(store: Store) -> None:
    if not store.passengers:
        print("No passengers saved.")
        return
    print("\nSaved passengers:")
    for i, p in enumerate(store.passengers, 1):
        print(f"  {i}. {p.name}, {p.age}, {p.gender}, berth: {p.berth}")


def list_journeys(store: Store) -> None:
    if not store.journeys:
        print("No journeys saved.")
        return
    print("\nSaved journeys:")
    for i, j in enumerate(store.journeys, 1):
        train = f" train {j.train_no}" if j.train_no else ""
        print(f"  {i}. {j.origin} -> {j.destination} on {j.date} [{j.travel_class}/{j.quota}]{train}")


def remove_item(items: list, label: str, store: Store) -> None:
    if not items:
        print(f"No {label} to remove.")
        return
    raw = ask(f"Enter {label} number to remove")
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        removed = items.pop(int(raw) - 1)
        save_store(store)
        print(f"Removed: {removed}")
    else:
        print("Invalid number.")


def tatkal_reminder(journey: Journey) -> None:
    if journey.quota != "TATKAL":
        return
    print("\n--- TATKAL TIMING ---")
    print(f"Tatkal booking opens: {tatkal_opening(journey):%d-%m-%Y %H:%M} IST.")
    now = datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S")
    print(f"Current IST time: {now}")
    print("Be logged in and on the booking page a minute early. Book manually.")


def select_passengers(store: Store) -> list[Passenger]:
    if not store.passengers:
        print("No saved passengers; add some first.")
        return []
    list_passengers(store)
    raw = ask("Passenger numbers (comma-separated, or 'all')", "all").lower()
    if raw == "all":
        return list(store.passengers)
    chosen: list[Passenger] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(store.passengers):
            passenger = store.passengers[int(part) - 1]
            if passenger not in chosen:
                chosen.append(passenger)
        else:
            raise ValueError(f"Invalid passenger number: {part!r}")
    return chosen


def choose_journey(store: Store) -> Journey | None:
    list_journeys(store)
    if not store.journeys:
        return None
    raw = ask("Pick journey number to prepare")
    if not (raw.isdigit() and 1 <= int(raw) <= len(store.journeys)):
        raise ValueError("Invalid journey number.")
    journey = store.journeys[int(raw) - 1]
    validate_journey(journey)
    if journey.quota == "TATKAL" and not journey.train_origin_date:
        journey.train_origin_date = ask("Train departure date at its originating station (DD-MM-YYYY)")
        tatkal_opening(journey)
        save_store(store)
    return journey


def booking_brief(journey: Journey, passengers: list[Passenger]) -> str:
    validate_journey(journey)
    maximum = 4 if journey.quota == "TATKAL" else 6
    if not 1 <= len(passengers) <= maximum:
        raise ValueError(f"Select between 1 and {maximum} passengers for this quota.")
    lines = ["BOOKING BRIEF", f"From: {journey.origin}", f"To: {journey.destination}",
             f"Boarding date: {journey.date}", f"Train: {journey.train_no or '(select on site)'}",
             f"Class: {journey.travel_class}", f"Quota: {journey.quota}", "", "Passengers:"]
    for number, passenger in enumerate(passengers, 1):
        validate_passenger(passenger)
        lines.append(f"{number}. {passenger.name}, {passenger.age}, {passenger.gender}, {passenger.berth}")
    if journey.quota == "TATKAL":
        lines.extend(["", f"Tatkal opens: {tatkal_opening(journey):%d-%m-%Y %H:%M} IST"])
    lines.extend(["", "Review all details, fare and availability on IRCTC before booking."])
    return "\n".join(lines) + "\n"


def export_reminder(journey: Journey, directory: Path | None = None) -> Path:
    opening = tatkal_opening(journey)
    if opening <= datetime.now(IST):
        raise ValueError("The Tatkal opening time has already passed.")
    directory = directory or DATA_FILE.parent / "exports"
    directory.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start = opening.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    end = (opening + timedelta(minutes=15)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Train Ticket Assistant//EN",
             "BEGIN:VEVENT", f"UID:{identifier}@train-ticket-assistant", f"DTSTAMP:{stamp}",
             f"DTSTART:{start}", f"DTEND:{end}", "SUMMARY:IRCTC Tatkal booking opens",
             f"URL:{IRCTC_URL}", "BEGIN:VALARM", "TRIGGER:-PT5M", "ACTION:DISPLAY",
             "DESCRIPTION:Prepare for IRCTC Tatkal booking", "END:VALARM", "END:VEVENT",
             "END:VCALENDAR", ""]
    path = directory / f"tatkal_{identifier}.ics"
    path.write_bytes("\r\n".join(lines).encode("utf-8"))
    return path


def prepare_booking(store: Store, *, automate: bool = False) -> None:
    journey = choose_journey(store)
    if journey is None:
        return
    picked = select_passengers(store)
    brief = booking_brief(journey, picked)
    print("\n" + brief)
    if ask_choice("Export this brief (contains passenger details)?", ["Yes", "No"], "No") == "Yes":
        directory = DATA_FILE.parent / "exports"
        directory.mkdir(exist_ok=True)
        path = directory / f"booking_{uuid.uuid4().hex}.txt"
        path.write_text(brief, encoding="utf-8")
        print(f"Saved: {path}")
    tatkal_reminder(journey)
    if automate:
        from irctc_browser import assist_booking
        assist_booking(journey, picked)
    elif ask_choice("Open IRCTC now in your browser?", ["Yes", "No"], "Yes") == "Yes":
        if webbrowser.open(IRCTC_URL):
            print("Opened IRCTC. Complete login, booking and payment manually.")
        else:
            print(f"Open this URL manually: {IRCTC_URL}")


def check_pnr_status() -> None:
    api_key = os.environ.get(PNR_API_KEY_ENV)
    api_url = os.environ.get(PNR_API_URL_ENV)
    api_host = os.environ.get(PNR_API_HOST_ENV)
    if not api_key or not api_url:
        print("\nPNR lookup needs your own authorized API.")
        print(f"Set env vars {PNR_API_KEY_ENV} and {PNR_API_URL_ENV} "
              f"(and {PNR_API_HOST_ENV} for RapidAPI) before using this.")
        print('Example: $env:PNR_API_URL="https://irctc1.p.rapidapi.com/api/v3/getPNRStatus?pnrNumber={pnr}"')
        return

    pnr = ask("Enter 10-digit PNR")
    if not (pnr.isascii() and pnr.isdigit() and len(pnr) == 10):
        print("PNR must be 10 digits.")
        return

    parsed_url = urllib.parse.urlsplit(api_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname or "{pnr}" not in api_url:
        raise ValueError("PNR_API_URL must be an HTTPS endpoint containing {pnr}.")
    if parsed_url.username or parsed_url.password or "{pnr}" in parsed_url.netloc:
        raise ValueError("PNR_API_URL must use a fixed host without embedded credentials.")
    url = api_url.replace("{pnr}", pnr)
    headers = {"x-rapidapi-key": api_key}
    if api_host:
        headers["x-rapidapi-host"] = api_host
    request = urllib.request.Request(url, headers=headers)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError("PNR API redirect refused; configure the final HTTPS endpoint.")

    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        print("\n--- PNR STATUS ---")
        print(json.dumps(data, indent=2))
    except urllib.error.HTTPError as exc:
        print(f"API error {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}")
    except json.JSONDecodeError:
        print("API returned a non-JSON response.")


MENU = """
Train Ticket Booking Assistant
  1. Add passenger
  2. List passengers
  3. Remove passenger
  4. Add journey
  5. List journeys
  6. Remove journey
  7. Prepare booking (brief + open IRCTC)
  8. Check PNR status
  9. Exit
    10. Assisted browser booking
    11. Export Tatkal calendar reminder (5 minutes before opening)
    12. Assisted booking from booking_config.toml
"""


def main() -> None:
    try:
        store = load_store()
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"Cannot load {DATA_FILE.name}: {exc}. The file has not been changed.")
        return
    while True:
        try:
            if not menu_action(store):
                break
        except (EOFError, KeyboardInterrupt):
            print("\nStopped. No booking was submitted by this assistant.")
            break
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Unable to complete action: {exc}")
            try:
                store = load_store()
            except (OSError, ValueError, TypeError, AttributeError):
                print("Cannot reload saved data. Stopping to avoid overwriting it.")
                break


def menu_action(store: Store) -> bool:
    print(MENU)
    choice = input("Choose an option: ").strip()
    if choice == "1":
        add_passenger(store)
    elif choice == "2":
        list_passengers(store)
    elif choice == "3":
        list_passengers(store)
        remove_item(store.passengers, "passenger", store)
    elif choice == "4":
        add_journey(store)
    elif choice == "5":
        list_journeys(store)
    elif choice == "6":
        list_journeys(store)
        remove_item(store.journeys, "journey", store)
    elif choice == "7":
        prepare_booking(store)
    elif choice == "8":
        check_pnr_status()
    elif choice == "9":
        print("Goodbye. Safe travels!")
        return False
    elif choice == "10":
        prepare_booking(store, automate=True)
    elif choice == "11":
        journey = choose_journey(store)
        if journey is not None:
            print(f"Saved: {export_reminder(journey)}")
            print("Import this .ics file into your calendar and enable notifications.")
    elif choice == "12":
        run_booking_config(CONFIG_FILE)
    else:
        print("Invalid option.")
    return True


def cli() -> int:
    parser = argparse.ArgumentParser(description="IRCTC preparation and supervised form filling.")
    parser.add_argument("--config", type=Path, help="Read journey and passengers from a TOML file.")
    parser.add_argument("--check-config", action="store_true", help="Validate config only; never open a browser.")
    arguments = parser.parse_args()
    if arguments.check_config and arguments.config is None:
        parser.error("--check-config requires --config")
    if arguments.config is None:
        main()
        return 0
    try:
        run_booking_config(arguments.config, check_only=arguments.check_config)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Unable to run config: {exc}")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nStopped. No booking was submitted by this assistant.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
