# Setting up single sign-on

Admin → **Sign-in methods**. Each method has a **callback URL** (shown after the first save) that must be
registered with the provider exactly as shown. It is built from `PUBLIC_URL` in `.env`, so set that to the
address people use (e.g. `https://bi.company.ae`) before registering.

Email and password always keep working, so an admin can still sign in if SSO is misconfigured.

## Try it locally first (no Microsoft or UAE PASS needed)

```bash
cd backend && python tools/dev_idp.py      # test provider on http://localhost:9000
```

Add **Other OpenID Connect**: discovery URL `http://localhost:9000/.well-known/openid-configuration`,
client ID `lumen-dev`, secret `dev-secret`. Its sign-in page offers three test people. Useful for trying
auto-create, domain limits, attribute and group mapping. (Only works when the API can reach localhost:9000,
i.e. when running without Docker.)

## Microsoft Entra ID (Office 365 / Azure AD)

In the [Microsoft Entra admin center](https://entra.microsoft.com):

1. **App registrations → New registration**
   - Name: `Lumen BI`
   - Supported account types: **Accounts in this organizational directory only (single tenant)**
   - Redirect URI: platform **Web**, value = Lumen's callback URL
2. From the app's **Overview**, copy the **Application (client) ID** and **Directory (tenant) ID**.
3. **Certificates & secrets → New client secret**. Copy the secret **Value** (not the Secret ID). Note its
   expiry date: sign-in stops working when it expires, so put a reminder in your calendar.
4. Optional, for group mapping: **Token configuration → Add groups claim** → Security groups. Microsoft then
   sends group **Object IDs**; in Lumen map each Object ID to a Lumen group name.
5. Optional, for attributes: **Token configuration → Add optional claim** (ID token), e.g. `department` is not
   standard; directory extension attributes appear as `extension_<appid>_<name>`.

In Lumen, add **Microsoft Entra ID** and fill in client ID, secret and tenant ID.

Notes:
- Users are identified by their Entra object ID (`oid`), so a changed email does not create a new account.
- If a user is in more than 200 groups, Microsoft omits the groups claim ("group overage"); assign only the
  groups that matter to the app (Token configuration → groups assigned to the application).

## UAE PASS

1. **Staging test**: client ID and secret are both `sandbox_stage`; any callback URL is accepted. Create a
   staging UAE PASS account with the staging app. This proves the flow end to end.
2. **Your own credentials**: apply as a service provider through the UAE PASS developer portal
   ([docs.uaepass.ae](https://docs.uaepass.ae)). You will be asked for the user journey and callback URL.
   Staging credentials for your entity are issued during onboarding.
3. **Go-live**: after UAE PASS approval, switch Environment to **Production** and enter production credentials.

Settings:
- **Minimum account level**: SOP1 = basic, unverified account (email and mobile verified by OTP);
  SOP2/SOP3 = Emirates ID verified. Choose SOP2 if only verified identities may sign in.
- **Page language**: English or Arabic for the UAE PASS screens.
- Attribute mapping can copy UAE PASS profile fields such as `nationalityEN` into data-access attributes.
  The Emirates ID number (`idn`) is not stored unless you map it; avoid that unless you need it.

Notes:
- UAE PASS has no ID token; Lumen reads the profile from its userinfo endpoint and identifies people by
  their UAE PASS `uuid`.
- Signing out of Lumen also signs the person out of UAE PASS, as UAE PASS requires.
- UAE PASS reviews the sign-in button design before go-live. Replace the button on the login page
  (`frontend/src/pages/Login.tsx`) with their official artwork from the developer portal.

## Who gets in

- **Invite only** (default): an admin adds the person first (Users → Add user, or CSV import). On first SSO
  sign-in they are matched by email and linked.
- **Create automatically**: anyone the provider signs in gets an account with the chosen role. Combine with
  **allowed email domains** for Microsoft/other OIDC.
- A person can link several methods (e.g. Microsoft at work and UAE PASS on mobile); see Users → edit.
