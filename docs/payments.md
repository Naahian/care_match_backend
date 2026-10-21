# SSLCOMMERZ payments

Uses the [official v4 hosted checkout and validation APIs](https://developer.sslcommerz.com/doc/v4/index.html).

Configure `.env`:

```dotenv
SSLCOMMERZ_STORE_ID=your-sandbox-store-id
SSLCOMMERZ_STORE_PASSWORD=your-sandbox-store-password
SSLCOMMERZ_SANDBOX=true
SSLCOMMERZ_CALLBACK_BASE_URL=https://your-public-backend.example.com
SSLCOMMERZ_TIMEOUT_SECONDS=30
```

The callback base URL is the backend origin, without `/api/v1`. Use a public HTTPS backend or tunnel; SSLCommerz cannot deliver callbacks to localhost. Callback URLs use the configured API prefix. Register `/api/v1/payments/sslcommerz/ipn` as the IPN listener in the merchant panel. For production, set `SSLCOMMERZ_SANDBOX=false` and use live store credentials.

A patient creates a checkout with `POST /api/v1/payments`, using their bearer token:

```json
{
  "appointment_id": "appointment-id",
  "amount_cents": 150000,
  "currency": "BDT",
  "idempotency_key": "unique-checkout-key",
  "customer_name": "Patient Name",
  "customer_email": "patient@example.com",
  "customer_phone": "01711111111",
  "customer_address": "Patient address",
  "customer_city": "Dhaka",
  "customer_postcode": "1200",
  "customer_country": "Bangladesh"
}
```

Customer contact/address fields are now required; country defaults to Bangladesh. Currency defaults to BDT and is normalized to uppercase. `amount_cents` remains the API's name for hundredths of the currency: `150000` means BDT 1,500.00. The current API accepts an amount from the client; callers must use the appointment's agreed charge. There is no fee/pricing model in this integration.

Open `data.checkout_url` in the response. Reusing a key with the same payload and payer returns the stored checkout; changing its details returns 409. Reservations are atomic across workers. A failed or interrupted initialization is not automatically retried because the gateway may already have created the session. Reconcile the transaction in the merchant panel before using a new key.

The gateway POSTs URL-encoded forms to the public `/api/v1/payments/sslcommerz/{success,fail,cancel,ipn}` endpoints. They return JSON in the existing success response format. Success/IPN callbacks call the validation API and compare transaction ID, validation ID, original currency and original amount before updating the record. Failed/cancelled callbacks require the gateway signature. High-risk or missing-risk transactions are held as `review`. Duplicate or late callbacks cannot downgrade `paid` or `review` records. The integration does not automatically confirm appointments or issue refunds.

The patient can poll `GET /api/v1/payments/{payment_id}` with their bearer token. Possible statuses are `initializing`, `initialization_failed`, `pending`, `paid`, `review`, `failed`, and `cancelled`. Check the stored status before fulfilling a payment. A browser redirect alone is not proof of payment.

Tests use a mocked gateway and in-memory repository. A real sandbox checkout still requires store credentials and a reachable backend.
