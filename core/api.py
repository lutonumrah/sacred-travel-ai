from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """Standard API error envelope for all DRF exceptions."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        response.data = {
            "success": False,
            "error": {
                "status_code": response.status_code,
                "detail": response.data,
            },
        }
        return response

    return Response(
        {
            "success": False,
            "error": {
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "detail": "An unexpected error occurred.",
            },
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


class SuccessResponse(Response):
    def __init__(self, data=None, message="OK", status_code=status.HTTP_200_OK, **kwargs):
        payload = {"success": True, "message": message, "data": data}
        super().__init__(payload, status=status_code, **kwargs)


class ErrorResponse(Response):
    """Hand-written error reply in the same envelope as `exception_handler`.

    `message` is kept at the top level because the chat widget shows it.
    """

    def __init__(self, message, status_code=status.HTTP_400_BAD_REQUEST, detail=None, **kwargs):
        payload = {
            "success": False,
            "message": message,
            "error": {
                "status_code": status_code,
                "detail": message if detail is None else detail,
            },
        }
        super().__init__(payload, status=status_code, **kwargs)


class EnvelopePagination(PageNumberPagination):
    """Page-number pagination that keeps the `success`/`data` envelope shape."""

    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            {
                "success": True,
                "message": "OK",
                "data": {
                    "results": data,
                    "count": self.page.paginator.count,
                    "page": self.page.number,
                    "pages": self.page.paginator.num_pages,
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
            }
        )


class EnvelopeMixin:
    """Wraps non-paginated DRF generic responses in the success envelope."""

    pagination_class = EnvelopePagination

    def finalize_response(self, request, response, *args, **kwargs):
        data = getattr(response, "data", None)
        already_wrapped = isinstance(data, dict) and (
            "success" in data and ("data" in data or "error" in data)
        )
        if response.status_code < 400 and not already_wrapped:
            response.data = {"success": True, "message": "OK", "data": data}
        return super().finalize_response(request, response, *args, **kwargs)
