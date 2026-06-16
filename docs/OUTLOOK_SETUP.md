# Outlook Calendar Integration — Setup

## Option A: Microsoft 365 Developer Program (Recommended)

A free, renewable E5 Developer sandbox subscription with a full enterprise tenant,
including pre-populated Outlook calendars for test users.

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
3. Search for: `Calendars.Read` → select it
4. **Add permissions**
5. **Grant admin consent** (available as tenant admin)

### 5. Fill in `.env`

```ini
MS_ENTRA_CLIENT_ID=<Client ID from step 3>
MS_ENTRA_TENANT_ID=<Tenant ID from step 3>
```

---

## Option B: Personal Microsoft Account

Simpler for local development without enterprise features.

1. Register an app as above, but under "Supported account types" choose:
   **"Personal Microsoft accounts only"**
2. After registration, go to **Authentication** → **"Allow public client flows"** → **Yes**
3. Tenant ID: `consumers`
4. API Permission: `Calendars.Read` (delegated)
5. No admin consent required

```ini
MS_ENTRA_CLIENT_ID=<Client ID>
MS_ENTRA_TENANT_ID=consumers
```

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

On subsequent
runs, authentication happens automatically (silent).

---

## Switching Back to Mock Data

Simply remove or comment out `MS_ENTRA_CLIENT_ID` from your `.env` file.
The Planner will fall back to `mock_data.USER_CALENDAR`.
