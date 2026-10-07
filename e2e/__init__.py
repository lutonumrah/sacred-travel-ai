"""End-to-end journeys through the real HTTP endpoints (FIP P8 integration testing).

Each test drives the system the way a customer's browser, a staff member's
browser or Razorpay would: the Django test client against the public widget,
pay-link, enquiry-form and webhook endpoints and the staff dashboard pages,
never the service layer directly. External services are stubbed only at their
outermost boundary (the Anthropic SDK client class, `urllib.request.urlopen`
for Gemini, the locmem email backend).
"""
