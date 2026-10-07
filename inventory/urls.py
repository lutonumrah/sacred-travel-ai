from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("destinations/", views.DestinationListView.as_view(), name="destinations"),
    path("destinations/add/", views.DestinationCreateView.as_view(), name="destination_create"),
    path(
        "destinations/<int:pk>/edit/",
        views.DestinationUpdateView.as_view(),
        name="destination_edit",
    ),
    path("hotels/", views.HotelListView.as_view(), name="hotels"),
    path("hotels/add/", views.HotelCreateView.as_view(), name="hotel_create"),
    path("hotels/<int:pk>/edit/", views.HotelUpdateView.as_view(), name="hotel_edit"),
    path("hotels/<int:pk>/offers/add/", views.HotelOfferCreateView.as_view(), name="offer_create"),
    path(
        "hotels/<int:pk>/offers/<int:offer_id>/edit/",
        views.HotelOfferUpdateView.as_view(),
        name="offer_edit",
    ),
    path(
        "hotels/<int:pk>/offers/<int:offer_id>/delete/",
        views.HotelOfferDeleteView.as_view(),
        name="offer_delete",
    ),
    path("cars/", views.CarListView.as_view(), name="cars"),
    path("cars/add/", views.CarCreateView.as_view(), name="car_create"),
    path("cars/<int:pk>/edit/", views.CarUpdateView.as_view(), name="car_edit"),
    path("packages/", views.PackageListView.as_view(), name="packages"),
    path("packages/add/", views.PackageCreateView.as_view(), name="package_create"),
    path("packages/<int:pk>/edit/", views.PackageUpdateView.as_view(), name="package_edit"),
    path("<str:kind>/<int:pk>/delete/", views.InventoryDeleteView.as_view(), name="delete"),
    path("<str:kind>/<int:pk>/toggle/", views.InventoryToggleView.as_view(), name="toggle"),
    path("visibility/", views.VisibilityView.as_view(), name="visibility"),
    path("visibility/save/", views.VisibilitySaveView.as_view(), name="visibility_save"),
    path("search/", views.InventorySearchView.as_view(), name="search"),
]
