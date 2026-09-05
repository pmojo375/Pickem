from pickem.log import clear_request_context, set_request_context, user_label_for


class RequestContextMiddleware:
    """Attach a short request id + user label to logs for this request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_request_context(user_label=user_label_for(getattr(request, "user", None)))
        try:
            return self.get_response(request)
        finally:
            clear_request_context()
