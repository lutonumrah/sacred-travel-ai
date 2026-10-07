from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from core.mixins import InventoryEditorMixin, PageMixin
from core.selectors import paginate

from . import selectors, services
from .forms import (
    CarRentalForm,
    DestinationForm,
    HotelForm,
    HotelOfferForm,
    InventoryFilterForm,
    InventorySearchForm,
    TourPackageForm,
    VisibilityForm,
)
from .models import CarRental, Destination, Hotel, HotelOffer, InventoryType, TourPackage


class _FilteredListView(PageMixin, TemplateView):
    """Shared list page for the three inventory kinds."""

    lister = None
    kind = ""
    active_nav = "inventory"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = InventoryFilterForm(self.request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound and form.is_valid() else {}
        queryset = type(self).lister(
            q=data.get("q", "") or "",
            destination=data.get("destination"),
            status=data.get("status", "") or "",
            min_price=data.get("min_price"),
            max_price=data.get("max_price"),
            include_archived=bool(data.get("archived")),
        )
        ctx["filter_form"] = form
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["total"] = queryset.count()
        ctx["kind"] = self.kind
        ctx["can_edit"] = _can_edit(self.request.user)
        return ctx


_LIST_ROUTE = {
    "hotel": "inventory:hotels",
    "car": "inventory:cars",
    "package": "inventory:packages",
    "destination": "inventory:destinations",
}


def _can_edit(user):
    return user.is_superuser or user.role in ("admin", "manager", "inventory")


class DestinationListView(PageMixin, TemplateView):
    template_name = "inventory/destinations.html"
    page_title = "Destinations"
    page_subtitle = "Master destination list for hotels, cars and packages."
    active_nav = "inventory"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        include_archived = self.request.GET.get("archived") == "on"
        queryset = selectors.list_destinations(
            q=self.request.GET.get("q", ""),
            status=self.request.GET.get("status", ""),
            include_archived=include_archived,
        )
        ctx["page_obj"] = paginate(queryset, self.request.GET.get("page"))
        ctx["q"] = self.request.GET.get("q", "")
        ctx["include_archived"] = include_archived
        ctx["can_edit"] = _can_edit(self.request.user)
        return ctx


class DestinationCreateView(InventoryEditorMixin, CreateView):
    model = Destination
    form_class = DestinationForm
    template_name = "inventory/destination_form.html"
    success_url = reverse_lazy("inventory:destinations")
    page_title = "Add Destination"
    page_subtitle = "Destinations power search, filters and AI recommendations."
    active_nav = "inventory"


class DestinationUpdateView(InventoryEditorMixin, UpdateView):
    model = Destination
    form_class = DestinationForm
    template_name = "inventory/destination_form.html"
    success_url = reverse_lazy("inventory:destinations")
    page_title = "Edit Destination"
    page_subtitle = "Update destination details."
    active_nav = "inventory"

    def get_queryset(self):
        return Destination.objects.filter(is_deleted=False)


class HotelListView(_FilteredListView):
    template_name = "inventory/hotels.html"
    page_title = "Hotel Management"
    page_subtitle = "Manage hotels, offers, pricing and core attributes."
    lister = staticmethod(selectors.list_hotels)
    kind = "hotel"


class CarListView(_FilteredListView):
    template_name = "inventory/cars.html"
    page_title = "Car Rental Management"
    page_subtitle = "Manage vehicles, rental details and pricing."
    lister = staticmethod(selectors.list_cars)
    kind = "car"


class PackageListView(_FilteredListView):
    template_name = "inventory/packages.html"
    page_title = "Tour Package Management"
    page_subtitle = "Manage packages, destinations, inclusions and pricing."
    lister = staticmethod(selectors.list_packages)
    kind = "package"


class _InventorySaveMixin(InventoryEditorMixin):
    kind = "item"

    def form_valid(self, form):
        created = self.object is None
        response = super().form_valid(form)
        services.record_inventory_saved(
            obj=self.object,
            actor=self.request.user,
            request=self.request,
            created=created,
            kind=self.kind,
        )
        messages.success(self.request, f"{self.object.name} saved.")
        return response


class HotelCreateView(_InventorySaveMixin, CreateView):
    model = Hotel
    form_class = HotelForm
    template_name = "inventory/hotel_form.html"
    success_url = reverse_lazy("inventory:hotels")
    page_title = "Add Hotel"
    page_subtitle = "Create hotel inventory."
    active_nav = "inventory"
    kind = "hotel"


class HotelUpdateView(_InventorySaveMixin, UpdateView):
    model = Hotel
    form_class = HotelForm
    template_name = "inventory/hotel_form.html"
    success_url = reverse_lazy("inventory:hotels")
    page_title = "Edit Hotel"
    page_subtitle = "Update hotel inventory and offers."
    active_nav = "inventory"
    kind = "hotel"

    def get_queryset(self):
        return Hotel.objects.filter(is_deleted=False)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["offers"] = self.object.offers.all()
        ctx["offer_form"] = HotelOfferForm()
        return ctx


class HotelOfferCreateView(InventoryEditorMixin, View):
    def post(self, request, pk):
        hotel = get_object_or_404(Hotel, pk=pk, is_deleted=False)
        form = HotelOfferForm(request.POST)
        if form.is_valid():
            offer = form.save(commit=False)
            offer.hotel = hotel
            offer.save()
            messages.success(request, "Offer added.")
        else:
            messages.error(request, "Could not add the offer — check the values.")
        return redirect("inventory:hotel_edit", pk=pk)


class HotelOfferUpdateView(InventoryEditorMixin, UpdateView):
    model = HotelOffer
    form_class = HotelOfferForm
    template_name = "inventory/offer_form.html"
    pk_url_kwarg = "offer_id"
    page_title = "Edit Room Offer"
    active_nav = "inventory"

    def get_queryset(self):
        return HotelOffer.objects.filter(
            hotel_id=self.kwargs["pk"], hotel__is_deleted=False
        ).select_related("hotel")

    def get_page_subtitle(self):
        return self.object.hotel.name

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Offer updated.")
        return redirect("inventory:hotel_edit", pk=self.kwargs["pk"])


class HotelOfferDeleteView(InventoryEditorMixin, View):
    def post(self, request, pk, offer_id):
        offer = get_object_or_404(HotelOffer, pk=offer_id, hotel_id=pk)
        offer.delete()
        messages.success(request, "Offer removed.")
        return redirect("inventory:hotel_edit", pk=pk)


class CarCreateView(_InventorySaveMixin, CreateView):
    model = CarRental
    form_class = CarRentalForm
    template_name = "inventory/car_form.html"
    success_url = reverse_lazy("inventory:cars")
    page_title = "Add Car Rental"
    page_subtitle = "Create car rental inventory."
    active_nav = "inventory"
    kind = "car"


class CarUpdateView(_InventorySaveMixin, UpdateView):
    model = CarRental
    form_class = CarRentalForm
    template_name = "inventory/car_form.html"
    success_url = reverse_lazy("inventory:cars")
    page_title = "Edit Car Rental"
    page_subtitle = "Update car rental inventory."
    active_nav = "inventory"
    kind = "car"

    def get_queryset(self):
        return CarRental.objects.filter(is_deleted=False)


class PackageCreateView(_InventorySaveMixin, CreateView):
    model = TourPackage
    form_class = TourPackageForm
    template_name = "inventory/package_form.html"
    success_url = reverse_lazy("inventory:packages")
    page_title = "Add Tour Package"
    page_subtitle = "Create tour package inventory."
    active_nav = "inventory"
    kind = "package"


class PackageUpdateView(_InventorySaveMixin, UpdateView):
    model = TourPackage
    form_class = TourPackageForm
    template_name = "inventory/package_form.html"
    success_url = reverse_lazy("inventory:packages")
    page_title = "Edit Tour Package"
    page_subtitle = "Update package details and itinerary."
    active_nav = "inventory"
    kind = "package"

    def get_queryset(self):
        return TourPackage.objects.filter(is_deleted=False)


def _back_to_list(kind, *, archived=False):
    url = reverse(_LIST_ROUTE.get(kind, "inventory:hotels"))
    return redirect(f"{url}?archived=on" if archived else url)


class _InventoryLifecycleView(InventoryEditorMixin, View):
    """POST-only archive / restore / delete for hotels, cars, packages and destinations."""

    action = None
    done = ""
    # Which list to land on afterwards: the archived view, or the normal one.
    show_archived = False

    def post(self, request, kind, pk):
        obj = selectors.get_managed_object(kind, pk)
        if obj is None:
            messages.error(request, "That item no longer exists.")
            return _back_to_list(kind)
        try:
            type(self).action(obj=obj, actor=request.user, request=request, kind=kind)
        except services.InventoryError as exc:
            messages.error(request, str(exc))
            return _back_to_list(kind, archived=obj.is_deleted)
        messages.success(request, f"{obj.name} {self.done}")
        return _back_to_list(kind, archived=self.show_archived)


class InventoryArchiveView(_InventoryLifecycleView):
    action = staticmethod(services.archive)
    done = "archived. It is hidden from lists, search and the AI; restore it any time."


class InventoryRestoreView(_InventoryLifecycleView):
    action = staticmethod(services.restore)
    done = "restored. It is inactive until you enable it."


class InventoryDeleteView(_InventoryLifecycleView):
    """Hard delete, only for archived items with no bookings."""

    action = staticmethod(services.delete_permanently)
    done = "deleted permanently."
    show_archived = True


class InventoryToggleView(InventoryEditorMixin, View):
    def post(self, request, kind, pk):
        obj = selectors.get_inventory_object(kind, pk)
        if obj is None:
            messages.error(request, "That item no longer exists.")
        else:
            services.toggle_active(obj=obj, actor=request.user, request=request, kind=kind)
            state = "activated" if obj.is_active else "deactivated"
            messages.success(request, f"{obj.name} {state}.")
        # Only bounce back to a page on this site; the Referer header is client-controlled.
        referer = request.META.get("HTTP_REFERER", "")
        if url_has_allowed_host_and_scheme(
            referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(referer)
        return redirect(_LIST_ROUTE.get(kind, "inventory:hotels"))


class VisibilityView(PageMixin, TemplateView):
    template_name = "inventory/visibility.html"
    page_title = "Inventory Visibility"
    page_subtitle = "Control which inventory appears on which website."
    active_nav = "inventory"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from websites.selectors import list_websites

        website_id = self.request.GET.get("website")
        website = list_websites().filter(pk=website_id).first() if website_id else None
        rows = selectors.list_visibility(
            website=website, inventory_type=self.request.GET.get("inventory_type", "")
        )
        resolved = []
        for row in rows:
            obj = selectors.get_inventory_object(row.inventory_type, row.object_id)
            resolved.append({"row": row, "object": obj})
        ctx["rows"] = resolved
        ctx["websites"] = list_websites(status="active")
        ctx["selected_website"] = website
        ctx["form"] = VisibilityForm()
        ctx["inventory_types"] = InventoryType.choices
        ctx["can_edit"] = _can_edit(self.request.user)
        return ctx


class VisibilitySaveView(InventoryEditorMixin, View):
    def post(self, request):
        form = VisibilityForm(request.POST)
        if form.is_valid():
            services.set_visibility(
                website=form.cleaned_data["website"],
                inventory_type=form.cleaned_data["inventory_type"],
                object_id=form.cleaned_data["object_id"],
                is_visible=form.cleaned_data["is_visible"],
                priority=form.cleaned_data["priority"],
                actor=request.user,
                request=request,
            )
            messages.success(request, "Visibility rule saved.")
        else:
            messages.error(request, form.errors.as_text())
        return redirect("inventory:visibility")


class InventorySearchView(PageMixin, TemplateView):
    template_name = "inventory/search.html"
    page_title = "Inventory Search"
    page_subtitle = "Structured search across hotels, cars and packages."
    active_nav = "inventory"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = InventorySearchForm(self.request.GET or None)
        results = []
        if form.is_valid():
            data = form.cleaned_data
            results = selectors.search_inventory(
                q=data.get("q", "") or "",
                inventory_type=data.get("inventory_type", "") or "",
                destination=data.get("destination", "") or "",
                min_price=data.get("min_price"),
                max_price=data.get("max_price"),
                website=data.get("website"),
                limit=50,
                check_in=data.get("check_in"),
                check_out=data.get("check_out"),
            )
        ctx["form"] = form
        ctx["results"] = results
        return ctx
