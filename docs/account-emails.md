# Firebase Authentication emails

## Setup

Both signup verification and password reset use Firebase Authentication's own
email templates and Google's email delivery service. Custom SMTP is not needed.

1. Set `FIREBASE_API_KEY` in `.env` to the project's Web API Key from Firebase
   console → Project settings → General. It must belong to the same project as
   `FIREBASE_CREDENTIALS_PATH`.
2. Enable Email/Password sign-in under Authentication → Sign-in method.
3. In Authentication → Templates → Email address verification, configure the
   sender name and supported template fields. Leave custom SMTP disabled to
   use Firebase's default delivery service.
4. Restart the backend after updating `.env`.

## Automatic verification on signup

Both `POST /api/v1/users/patient` and `POST /api/v1/users/doctor` request an
email using the **Email address verification** template after account creation
(and doctor profile creation, when applicable).

The backend creates a Firebase Admin custom token for the new account's uid,
exchanges it for a Firebase ID token, then calls `accounts:sendOobCode` with
`requestType: VERIFY_EMAIL`. Firebase sends the email to that account's address.
The password is not used for this process. Tokens and verification links are
never returned by signup.

Signup returns `201` with `status`, `msg`, `data` (the user), and
`verification_email_sent`. If sending fails, the account remains created and
`verification_email_sent` is `false`; the server logs the failure stage. A true
value means Firebase accepted the send request, not that inbox delivery is
confirmed. Following the emailed link verifies the email in Firebase. Refresh
the client's ID token afterward to update its `email_verified` claim.

There is no standalone `/users/verify-email` endpoint.

## Password reset

```http
POST /api/v1/users/reset-password
Content-Type: application/json

{"email":"user@example.com"}
```

Authentication is not required. Firebase sends its Password reset template.
Success returns `200` with:

```json
{
  "status": 200,
  "msg": "If an account exists for this email, a password reset email has been sent",
  "data": null
}
```

Unknown or disabled accounts receive the same response. The user follows the
emailed link to choose a new password in Firebase's hosted action handler.
Missing configuration returns `503`, upstream failures return `502`, Firebase
throttling returns `429`, and invalid request payloads return `422`.

References: [Firebase Auth REST API](https://firebase.google.com/docs/reference/rest/auth),
[Firebase email templates](https://support.google.com/firebase/answer/7000714).
