# Onboarding & Profil

Eigenständige Web-App, die den **Onboarding-Flow im Look & Feel des DB Navigators**
nachbaut: DB-Konto anmelden, Reisen importieren, Präferenzen und harte Grenzen
erfassen, Benachrichtigungen und Autonomiegrad festlegen. Das dabei entstehende
**Nutzerprofil** ist die Wissensbasis, gegen die der Journey-Autopilot später
Umplanungen bewertet.

> **Warum eine eigene App und kein echtes DB-Navigator-Plugin?** Die DB bietet
> Dritten keine offizielle API/Erweiterungsschnittstelle für Konto-Login oder
> Ticket-Import. Wir bauen die UX deshalb als eigenständige App im
> DB-Navigator-Design nach — die Backends sind simuliert, die API-Verträge
> entsprechen aber dem, was eine echte Anbindung liefern müsste. Hintergrund
> siehe [`CONTEXT_RECORD.md`](../CONTEXT_RECORD.md) → *Onboarding & Profile*.

---

## Schnellstart

```bash
# aus dem Projektwurzelverzeichnis, in aktivierter Umgebung
pip install -r requirements.txt        # FastAPI, uvicorn, requests, pydantic
python run_onboarding.py               # -> http://127.0.0.1:8000
```

Browser öffnen: **http://127.0.0.1:8000** — am besten in der mobilen
Geräteansicht (DevTools → Responsive), dann wirkt die App wie der DB Navigator
am Handy.

**Demo-Login** (wird auch direkt auf dem Login-Screen angezeigt):

| E-Mail                     | Passwort  | Konto                                   |
| -------------------------- | --------- | --------------------------------------- |
| `lucas.wild@example.com`   | `demo123` | BahnCard 50, BahnBonus Gold · 3 Reisen  |
| `erika.muster@example.com` | `demo123` | BahnCard 25, BahnBonus Silber · 1 Reise |

Optional vorher den **DB-Live-Daten-Sidecar** starten, dann nutzt die
Heimatbahnhof-Suche echte DB-Stationsdaten statt der Fallback-Liste:

```bash
cd db_service && npm install && npm start    # liefert /locations auf :3000
```

Konfiguration über Umgebungsvariablen:

| Variable          | Default                          | Zweck                                  |
| ----------------- | -------------------------------- | -------------------------------------- |
| `ONBOARDING_HOST` | `127.0.0.1`                      | Bind-Host der Web-App                  |
| `ONBOARDING_PORT` | `8000`                           | Port der Web-App                       |
| `DB_API_URL`      | `http://127.0.0.1:3000`          | Endpunkt des `db_service`-Sidecars     |
| `JA_DB_PATH`      | `data/journey_autopilot.db`      | SQLite-Datei (von `.gitignore` erfasst)|

---

## Der Onboarding-Flow

| # | Schritt           | Pflicht? | Inhalt                                                                          |
| - | ----------------- | -------- | ------------------------------------------------------------------------------ |
| 0 | **Willkommen**    | –        | Nutzenversprechen, Datenschutzhinweis                                           |
| 1 | **DB-Konto**      | Pflicht  | bahn.de-Login → importiert Reisen + BahnCard/BahnBonus                          |
| 2 | **Reisen**        | –        | Übersicht der importierten Buchungen (ab jetzt überwacht)                       |
| 3 | **Mobilnummer**   | optional | SMS-Code-Verifizierung für Warnungen per SMS/WhatsApp                           |
| 4 | **Outlook**       | optional | Kalender-Consent (Microsoft) → schützt harte Termine bei Umplanungen           |
| 5 | **Präferenzen**   | –        | Klasse, Sitzplatz, Ruhebereich, Tempo-vs.-Komfort, max. Umstiege               |
| 6 | **Zuhause**       | –        | Heimatbahnhof (Live-Autocomplete), späteste Heimkehr, Hotel-/Taxi-OK           |
| 7 | **Benachrichtigung** | –     | Kanäle, Ruhezeiten und **Autonomiegrad** (nur informieren / freigeben / auto)  |
| 8 | **Zusammenfassung** | –      | Abschluss → Dashboard                                                           |

DB-Login ist die einzige Pflicht (ohne importierte Reisen gibt es nichts zu
überwachen). Alle anderen Schritte sind überspringbar bzw. mit sinnvollen
Defaults vorbelegt — der Wizard blockiert nie. Jeder Schritt speichert nur den
geänderten Profil-Teil als Patch (`PUT /api/profile`); Abbrechen und später
Weitermachen ist jederzeit möglich.

Nach dem Abschluss landet man im **Dashboard** (DB-Navigator-Hauptansicht mit
gemockter Tableiste *Buchen / Umgebung / Reisen / Profil*): überwachte Reisen,
Profilzusammenfassung, Verbindungen und die DSGVO-Löschung.

---

## Was ist echt, was simuliert?

| Funktion                  | Status        | Hinweis                                                                |
| ------------------------- | ------------- | ---------------------------------------------------------------------- |
| Heimatbahnhof-Suche       | **live**\*    | echte DB-Stationsdaten via `db_service`-Sidecar, sonst Fallback-Liste  |
| Profil-Persistenz         | **echt**      | SQLite (`onboarding/store.py`)                                          |
| DB-Konto-Login & Import   | *simuliert*   | `onboarding/accounts.py` — keine offizielle DB-API vorhanden           |
| SMS-Verifizierung         | *simuliert*   | kein SMS-Gateway; der Code wird als Einblendung zurückgegeben          |
| Outlook-Kalender (OAuth)  | *simuliert*   | keine registrierte Microsoft-App; Consent-Dialog + Beispieltermine     |

\* nur wenn der Sidecar läuft. Ein grüner Punkt vor einem Treffer markiert
Live-Daten; ohne Sidecar greift die statische Liste großer Bahnhöfe.

Die Simulationen liegen bewusst hinter realistischen API-Verträgen. Eine echte
Integration tauscht im Idealfall nur `accounts.py` und die betroffenen
Endpunkte — der Rest (UI, Store, Profilstruktur) bleibt unberührt.

---

## Architektur

```
onboarding/
├── server.py        FastAPI-App: JSON-API + Auslieferung der statischen UI
├── accounts.py      Simulierte DB-Konten, Buchungen, Outlook-Termine, Bahnhof-Fallback
├── store.py         SQLite-Store: Nutzer, Profil (JSON-Blob), importierte Reisen
├── __init__.py      Paketdoku (Berührungspunkt zu journey_autopilot ist nur der Store)
└── static/
    ├── index.html   DB-Navigator-Rahmen: Statusleiste, Kopfzeile, Tableiste, DB-Logo
    ├── style.css    DB-Navigator-Dark-Theme (DB-Rot #EC0016, dunkle Slate-Flächen)
    └── app.js       Framework-loser Wizard: render(step) + Schritt-für-Schritt-Patches
```

- **Kein Build-Schritt, kein JS-Framework.** Die UI ist Vanilla JS; `render(step)`
  zeichnet den jeweiligen Screen, die untere Navigation wird pro Schritt
  konfiguriert.
- **Sessions** liegen in-memory (Token → `user_id`). Ein Neustart heißt einfach
  „neu einloggen“ — für den Single-User-Prototyp bewusst ohne Persistenz.
- **Profil** wird als ein JSON-Blob pro Nutzer gespeichert (prototyp-freundlich,
  keine Migrationen). Die Struktur inkl. Defaults steht in
  [`store.py`](store.py) → `DEFAULT_PROFILE`.

### Anbindung an die Agenten

`journey_autopilot.tools` liest dasselbe SQLite über
`onboarding.store.any_profile()` (Single-User: „das zuletzt gepflegte Profil“).
Der Planner-Agent rankt Reroute-Optionen gegen dieses Profil — ohne FastAPI-
Abhängigkeit, der gemeinsame Berührungspunkt ist allein der Store.

---

## API-Überblick

| Methode & Pfad                    | Zweck                                                       |
| --------------------------------- | ----------------------------------------------------------- |
| `POST /api/auth/db-login`         | Simulierter Login → Token + Konto + importierte Reisen      |
| `GET  /api/me`                    | Konto, Profil und Reisen der aktuellen Session              |
| `GET  /api/trips`                 | Importierte Reisen                                          |
| `POST /api/verify/phone/start`    | SMS-Code anfordern (Demo: Code kommt in der Antwort)        |
| `POST /api/verify/phone/confirm`  | Code bestätigen                                             |
| `POST /api/connect/outlook`       | Outlook-Consent erteilen → Beispieltermine                  |
| `DELETE /api/connect/outlook`     | Outlook-Verbindung trennen                                  |
| `GET  /api/profile`               | Profil lesen                                                |
| `PUT  /api/profile`               | Teil-Patch ins Profil mergen (pro Onboarding-Schritt)       |
| `POST /api/onboarding/complete`   | Onboarding als abgeschlossen markieren                      |
| `DELETE /api/profile`             | **DSGVO-Löschung:** Konto, Profil und Reisen entfernen      |
| `GET  /api/stations?query=`       | Bahnhofssuche (Live via Sidecar, sonst Fallback)            |

Geschützte Endpunkte erwarten den Header `Authorization: Bearer <token>` aus dem
Login. Interaktive API-Doku unter **/docs** (FastAPI/Swagger).

---

## Design

Die UI kopiert das Erscheinungsbild des **DB Navigators (Dark Mode)**:

- **DB-Logo** als rote Rahmenmarke (SVG) in Kopfzeile, Reise-Karten und auf dem
  Willkommens-Screen.
- **DB-Rot `#EC0016`** als Marken- und Aktionsfarbe, dunkle Slate-Flächen,
  weiße Schrift, grüne Status-Punkte.
- **Reise-Karten** im Navigator-Layout: DB-Logo + Zug, Start-/Ziel-Marker mit
  Punkt-/Pin-Symbolen, Datum/Zeit, QR-Kachel.
- **Gemockter Rahmen** (nicht Teil des Onboardings, rein visuell): iOS-Status-
  leiste oben und die Tableiste *Buchen / Umgebung / Reisen / Profil* unten.

Die Hausschrift „DB Sans“ ist lizenzpflichtig; ohne Lizenz greift der
System-Font-Fallback.

---

## Datenschutz

- **Datensparsamkeit:** Es werden nur die fürs Onboarding nötigen Felder erfasst;
  alles liegt lokal in der SQLite-Datei.
- **Volle Kontrolle:** Profil jederzeit im Dashboard einsehbar und änderbar.
- **Recht auf Löschung (DSGVO Art. 17):** Ein Klick auf *Profil & Daten löschen*
  entfernt Konto, Profil und importierte Reisen unwiderruflich
  (`DELETE /api/profile`).
