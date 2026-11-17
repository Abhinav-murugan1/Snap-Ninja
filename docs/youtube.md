# Connect YouTube

[Back to README](../README.md)

This walkthrough is also available inside **Setup guide → Start editing → Set up
YouTube uploads**, and in the upload dialog. The setup guide lets you connect before
creating a clip. Uploading requires OAuth authorization; an API key alone is insufficient.

## 1. Create a project

Open https://console.cloud.google.com and create a project, for example **Snap Ninja
uploads**. Keep it selected for every step below. Use a Google account that owns a
YouTube channel, or has the appropriate channel access.

## 2. Enable the API

Open **APIs & Services → Library**, search for **YouTube Data API v3**, and enable it.
Direct page: https://console.cloud.google.com/apis/library/youtube.googleapis.com

## 3. Configure consent

Open **Google Auth platform → Branding**, or **OAuth consent screen** in older layouts.
Use **Get started** if needed. Supply your app name, support email and contact email,
and review Google's terms yourself.

For a personal account, choose **External** and keep it in **Testing** while setting it
up. In **Audience → Test users**, add the Google account you will use for the channel.

In **Data Access → Add or remove scopes**, declare these permissions and save:

| Scope | Snap Ninja uses it for |
| --- | --- |
| `https://www.googleapis.com/auth/youtube.upload` | Uploading the selected video |
| `https://www.googleapis.com/auth/youtube.readonly` | Reading the connected channel details |

## 4. Create a Desktop app client

Open **Google Auth platform → Clients → Create client**. Older layouts use **APIs &
Services → Credentials → Create credentials → OAuth client ID**.

Choose **Desktop app**, name the client, create it, and download the JSON file. Do not
use a Web application client, service account or API key. Desktop clients handle the
local sign-in callback without a website redirect URL that you need to configure.

## 5. Add the file and sign in

In Snap Ninja, choose the downloaded JSON under **Google OAuth client file (.json)**.
It can keep its downloaded filename. Choose **Sign in with Google**, then review the
account, channel and permissions in your browser. Return to the app after sign-in.

The client is stored as `youtube/client_secret.json`; saved authorization is stored
as `youtube/token.json`. Never publish these files. Snap Ninja never asks you to type
your Google password into the app. Connecting does not upload a video.

## 6. Make a test upload

On a finished clip choose **Upload to YouTube**. Confirm the channel, format, title,
description and visibility. Choose **Private** for your first test, then explicitly
start the upload. Inspect the completed result in YouTube Studio.

## Troubleshooting

- **Access blocked:** check Audience → Test users and use the same account during sign-in.
- **Unverified app:** check the project owner and app name; review Google's testing
  guidance before granting access to your own test project. Do not authorize unfamiliar apps.
- **Client type/redirect error:** create a Desktop app client and download a fresh JSON.
- **Expired sign-in:** External/Testing tokens for these permissions normally expire
  after seven days. Reconnect. Production distribution has separate verification rules.
- **API disabled:** enable YouTube Data API v3 in the project identified by the client file.
- **Quota exceeded:** inspect that API's Quotas page for your allowance and reset time;
  channel limits can also apply.
- **Wrong channel:** use **Use a different channel** in the upload dialog and reconnect.
- **Organization blocks access:** ask the Google Workspace administrator about its policy.

Google can rename console menus and change limits. Official references:

- https://developers.google.com/youtube/data/guides/authentication
- https://developers.google.com/workspace/guides/configure-oauth-consent
- https://developers.google.com/youtube/data/guides/uploading_a_video
