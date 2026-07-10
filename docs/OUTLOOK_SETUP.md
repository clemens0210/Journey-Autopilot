# Outlook Calendar Integration — Setup

## Option A: Microsoft 365 Developer Program

A free, renewable E5 Developer sandbox subscription with a full enterprise tenant,
including pre-populated Outlook calendars for test users.

> **Note:** Developer Program eligibility is now restricted (it often requires a
> qualifying Visual Studio subscription). If you just want to connect your own
> calendar, **Option B works with a regular personal Microsoft account** and is
> the simpler path.

### 1. Join the Developer Program

1. Go to https://developer.microsoft.com/microsoft-365/dev-program
2. Sign in with a personal Microsoft account (e.g. `@outlook.com`)
3. Click "Join now" → fill out the form (Company: "Journey Autopilot Dev",
   Region: any, Focus: "Microsoft 365 Apps")
4. After confirmation: "Set up E5 subscription" → choose a country, pick an admin
   username, note the password
5. Write down the `.onmicrosoft.com` domain, e.g.
   `journeyautopilotdev.onmicrosoft.com`

### 2. Register an App in the Entra Admin Center

1. Open https://entra.microsoft.com/ with your new admin account
2. **Identity > Applications > App registrations** → **New registration**
3. Name: `Journey Autopilot`
4. Supported account types:
   - **Accounts in this organizational directory only** (for Developer tenants)
5. Redirect URI: leave blank (device-code flow does not need one)
6. **Register**
7. After registration, go to **Authentication** → under **Advanced settings**
   at the bottom → set **"Allow public client flows"** to **Yes** → **Save**
   *(Required for device-code authentication to work.)*

### 3. Note the Client ID and Tenant ID

- **Client ID**: Under "Essentials" → "Application (client) ID"
- **Tenant ID**: Under "Essentials" → "Directory (tenant) ID"

### 4. Configure API Permissions

1. **API Permissions** → **Add a permission** → **Microsoft Graph**
2. **Delegated permissions** (not Application)
3. Add **all three**:
   - `Calendars.Read` — read the connected calendar
   - `User.Read` — read the signed-in user's own profile (email + name), so the
     app shows/uses the **actual** connected account instead of a demo email
   - `Mail.Send` — send the (user-approved) notice email to the contact of a
     clashing calendar appointment
4. **Add permissions**
5. **Grant admin consent** (available as tenant admin)

### 5. Fill in `.env`

```ini
MS_ENTRA_CLIENT_ID=<Client ID from step 3>
MS_ENTRA_TENANT_ID=<Tenant ID from step 3>
```

---

## Option B: Personal Microsoft Account (Recommended)

Simpler for local development without enterprise features — connects your own
calendar with a regular Microsoft account. No paid Azure subscription required.

1. Register an app as above, but under "Supported account types" choose:
   **"Personal Microsoft accounts only"** (or "Accounts in any organizational
   directory and personal Microsoft accounts" if you also want work accounts).
2. After registration, go to **Authentication** → **"Allow public client flows"** → **Yes**
3. Tenant ID: `consumers` (see the account-type table below)
4. API Permissions (delegated): `Calendars.Read`, `User.Read` **and** `Mail.Send`
5. No admin consent required — you approve the scopes yourself at sign-in

> **Added `Mail.Send` later?** Existing cached logins have not consented to
> it: calendar reading keeps working, but sending the notice email fails with
> `AuthenticationRequiredError` until you **reconnect Outlook once**
> (onboarding UI or `python scripts/check_outlook.py --login`) — the
> interactive login requests the full scope set including `Mail.Send`.

```ini
MS_ENTRA_CLIENT_ID=<Client ID>
MS_ENTRA_TENANT_ID=consumers
```

---

## Choosing the right `MS_ENTRA_TENANT_ID`

`MS_ENTRA_TENANT_ID` **must match the app's "Supported account types"**, or
sign-in fails. The app registration can live in any directory you control (e.g.
a free Entra tenant) — the calendar data always comes from whoever actually
signs in at the device-code prompt.

| App "Supported account types"                       | `MS_ENTRA_TENANT_ID` |
| --------------------------------------------------- | -------------------- |
| Personal Microsoft accounts only                    | `consumers`          |
| Accounts in this organizational directory only      | the Directory (tenant) ID (GUID) |
| Any organizational directory **+** personal accounts | `common`             |

> **`AADSTS9002346: … configured for use by Microsoft Account users only.
> Please use the /consumers endpoint`** — you set a tenant GUID for a
> *personal-accounts-only* app. Fix: set `MS_ENTRA_TENANT_ID=consumers`.

No paid Azure subscription is needed to register the app — Microsoft Entra app
registrations are free. If the portal won't let you register (it asks for an
M365 Developer account or an Azure subscription), create a **free Entra tenant**
(entra.microsoft.com → **Manage tenants → Create**) and register the app there.

---

## Marking Calendar Events as Hard Constraints

For the Planner Agent to recognise an appointment as non-negotiable, the event
in Outlook must carry the **`Journey-Autopilot/Hard`** category:

1. Open the event in Outlook (Web or Desktop)
2. **Categorize** → **All Categories** → **New**
3. Name: `Journey-Autopilot/Hard`, pick any colour
4. Assign the category to the event

Events without this category are treated as reschedulable
(`hard_constraint: False`).

---

## First Run (Device-Code Authentication)

On the first call to `get_user_calendar` with real credentials, the terminal
will display:

```
To sign in, use a web browser to open the page
https://microsoft.com/devicelogin
and enter the code: ABC123DEF
```

1. Open the URL in a browser
2. Enter the code
3. Sign in with the account whose calendar you want to query
4. Click "Next" → "Sign in" → wait for confirmation

On subsequent runs, authentication happens automatically (silent). Two files
make that work, both under `%LOCALAPPDATA%\.IdentityService` (Windows) or
`~/.IdentityService` (Linux/macOS):

- the **MSAL token cache** (`journey_autopilot.nocae` / `.cae`) — encrypted
  tokens that survive restarts
- the **authentication record** (`journey_autopilot.authrecord.json`) — plain
  account metadata (no secrets) saved by the onboarding login; without it a
  fresh credential cannot locate the cached account and the agent tools fall
  back to mock data with
  `AuthenticationRequiredError: Interactive authentication is required`

If you see that error in the logs (e.g. after connecting with an older version
that didn't save the record, or after the record was deleted), **reconnect
Outlook once** in the onboarding UI — the record is (re)written on every
successful login. "Disconnect Outlook" removes both files.

---

## Switching Back to Mock Data

Simply remove or comment out `MS_ENTRA_CLIENT_ID` from your `.env` file.
The Planner will fall back to `mock_data.USER_CALENDAR`.
