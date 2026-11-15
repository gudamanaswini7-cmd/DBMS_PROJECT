# EcoCycle E-Waste Management System

A responsive Flask and SQLite project for citizen e-waste submissions, staff-managed collections, and eco-reward transactions.

## Run locally

```powershell
python -m pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The app initializes `ewaste.db` on startup and migrates existing user, item, and pickup data into the current schema.

## Demo accounts

| Category | Email | Password |
|---|---|---|
| Staff | `staff@test.com` | `123` |
| Citizen | `priya@test.com` | `123` |

Anyone can create an account with their name, email, and password; public registrations receive the citizen role. Sign-in requires only email and password and automatically routes each account to its dashboard. Staff access remains limited to staff accounts. Passwords are stored as hashes.

## Routes and modules

- `/register`, `/login`, `/logout`: account registration, category-validated login, and sign out.
- `/citizen/dashboard`: waste-count, weight, rewards, and recent item dashboard.
- `/citizen/payment`: enter a UPI ID and launch a UPI payment intent without a preset amount.
- `/citizen/new-waste`: item type, weight, description, photo, pickup address, and pickup date form.
- `/staff/dashboard`: citizen and collection statistics with a recovery chart.
- `/staff/collections`: pending pickup queue and completion actions.
- `/staff/transactions`: completed collections and rewards.

Completing a collection records one transaction and rewards the citizen at **$2.50 per kilogram**. Photos are restricted to JPG, PNG, or WEBP and stored in `static/uploads` (8 MB maximum).

The UPI module hands off to a compatible UPI app and does not process, store, or verify payments. Citizens must verify the recipient and amount in their payment app.

## Database

`database.sql` defines `users`, `ewaste_items`, `collections`, and `transactions`. SQLite `CHECK` constraints implement the requested category and status enums. The `image_path` column on e-waste items stores the uploaded photo path.

The UI uses Tailwind CSS from its CDN and Lucide icons. Flask's built-in development server is intended for local demos, not production deployments.
