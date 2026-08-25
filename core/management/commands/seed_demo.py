"""Populate the database with a realistic demo dataset.

    python manage.py seed_demo

Idempotent: re-running updates the same records rather than duplicating them.
Pass --reset to clear the demo data first.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Team, User
from bookings import services as booking_services
from bookings.models import Booking, BookingStatus, Notification, Payment
from conversations import services as conversation_services
from conversations.models import Conversation
from crm import services as crm_services
from crm.models import Customer, FollowUpTask, Lead, LeadStatus
from inventory.models import CarRental, Destination, Hotel, HotelOffer, TourPackage
from websites.models import Website
from websites.services import issue_api_key

DESTINATIONS = [
    ("Goa", "goa", "Panaji", "Goa"),
    ("Manali", "manali", "Manali", "Himachal Pradesh"),
    ("Jaipur", "jaipur", "Jaipur", "Rajasthan"),
    ("Kerala", "kerala", "Kochi", "Kerala"),
    ("Andaman", "andaman", "Port Blair", "Andaman & Nicobar"),
    ("Rishikesh", "rishikesh", "Rishikesh", "Uttarakhand"),
]

HOTELS = [
    ("Sunset Beach Resort", "goa", 4, 6500, ["Wi-Fi", "Pool", "Breakfast", "Beach access"]),
    ("Palolem Palm Stay", "goa", 3, 3200, ["Wi-Fi", "Breakfast"]),
    ("Snow Ridge Retreat", "manali", 4, 5400, ["Wi-Fi", "Heater", "Mountain view"]),
    ("Old Manali Lodge", "manali", 3, 2600, ["Wi-Fi", "Cafe"]),
    ("Amber Heritage Haveli", "jaipur", 5, 11500, ["Wi-Fi", "Pool", "Spa", "Airport pickup"]),
    ("Pink City Inn", "jaipur", 3, 2900, ["Wi-Fi", "Breakfast"]),
    ("Backwater Villa", "kerala", 4, 7200, ["Wi-Fi", "Houseboat transfer", "Breakfast"]),
    ("Havelock Sands", "andaman", 4, 8900, ["Wi-Fi", "Scuba desk", "Breakfast"]),
    ("Ganga View Ashram Stay", "rishikesh", 3, 2200, ["Yoga", "Wi-Fi"]),
]

CARS = [
    ("Goa Beach Hatchback", "goa", "Hatchback", "Maruti", "Swift", 5, 1400),
    ("Goa Cruiser SUV", "goa", "SUV", "Mahindra", "Scorpio", 7, 2900),
    ("Manali Mountain SUV", "manali", "SUV", "Toyota", "Innova", 7, 3400),
    ("Jaipur City Sedan", "jaipur", "Sedan", "Honda", "City", 5, 2100),
    ("Kerala Tempo Traveller", "kerala", "Van", "Force", "Traveller", 12, 4800),
]

PACKAGES = [
    ("Goa Beach Escape", "goa", 4, 3, 24000, "Stay, breakfast, airport transfers, one cruise"),
    ("Manali Adventure Week", "manali", 6, 5, 38000, "Stay, meals, Solang activities, transfers"),
    ("Royal Rajasthan Circuit", "jaipur", 7, 6, 62000, "Heritage stays, guide, all transfers"),
    ("Kerala Backwaters & Hills", "kerala", 6, 5, 47000, "Houseboat night, hill stay, transfers"),
    ("Andaman Island Hopper", "andaman", 5, 4, 55000, "Ferries, stay, snorkelling"),
    ("Rishikesh Yoga Retreat", "rishikesh", 4, 3, 18000, "Yoga sessions, meals, stay"),
]

CUSTOMERS = [
    ("Ananya", "Sharma", "ananya.sharma@example.com", "9810012345", "Delhi"),
    ("Rohit", "Verma", "rohit.verma@example.com", "9820023456", "Mumbai"),
    ("Meera", "Nair", "meera.nair@example.com", "9830034567", "Bengaluru"),
    ("Imran", "Qureshi", "imran.qureshi@example.com", "9840045678", "Hyderabad"),
    ("Priya", "Desai", "priya.desai@example.com", "9850056789", "Pune"),
    ("Karan", "Malhotra", "karan.malhotra@example.com", "9860067890", "Chandigarh"),
]

STAFF = [
    ("admin", "Aarav", "Kapoor", "admin"),
    ("manager", "Divya", "Rao", "manager"),
    ("agent1", "Sameer", "Khan", "employee"),
    ("agent2", "Neha", "Gupta", "employee"),
    ("stock", "Vikram", "Singh", "inventory"),
]


class Command(BaseCommand):
    help = "Seed the database with demo websites, inventory, customers, leads and bookings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo records before seeding.",
        )
        parser.add_argument(
            "--password",
            default="travel1234",
            help="Password for the demo staff accounts (default: travel1234).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260818)  # deterministic demo data

        if options["reset"]:
            self.stdout.write("Clearing existing demo data…")
            Payment.objects.all().delete()
            Booking.objects.all().delete()
            Notification.objects.all().delete()
            Conversation.objects.all().delete()
            FollowUpTask.objects.all().delete()
            Lead.objects.all().delete()
            Customer.objects.all().delete()
            HotelOffer.objects.all().delete()
            Hotel.objects.all().delete()
            CarRental.objects.all().delete()
            TourPackage.objects.all().delete()
            Destination.objects.all().delete()
            Website.objects.all().delete()

        staff = self._seed_staff(options["password"])
        websites = self._seed_websites()
        destinations = self._seed_destinations()
        self._seed_inventory(destinations)
        customers = self._seed_customers()
        leads = self._seed_leads(customers, websites, staff)
        self._seed_conversations(websites, customers)
        self._seed_bookings(leads, staff)

        self.stdout.write(self.style.SUCCESS("\nDemo data ready."))
        self.stdout.write(
            f"  Sign in as any of: {', '.join(name for name, *_ in STAFF)} "
            f"(password: {options['password']})"
        )
        self.stdout.write("  Try the widget at /conversations/widget/")

    # -- steps ------------------------------------------------------------

    def _seed_staff(self, password):
        created = {}
        for username, first, last, role in STAFF:
            user, is_new = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "email": f"{username}@scaredtravel.example",
                    "role": role,
                    "is_staff": role == "admin",
                    "is_superuser": role == "admin",
                },
            )
            if is_new:
                user.set_password(password)
                user.save()
            created[username] = user

        sales, _ = Team.objects.get_or_create(
            name="Sales", defaults={"description": "Handles inbound travel leads."}
        )
        sales.members.set([created["manager"], created["agent1"], created["agent2"]])
        self.stdout.write(f"  {len(created)} staff users")
        return created

    def _seed_websites(self):
        specs = [
            ("Scared Travel", "scaredtravel.com", "scared-main", "#0F766E"),
            ("Himalaya Holidays", "himalayaholidays.com", "himalaya", "#B45309"),
        ]
        websites = []
        for name, domain, source, colour in specs:
            website, created = Website.objects.get_or_create(
                source_identifier=source,
                defaults={
                    "name": name,
                    "brand_name": name,
                    "domain": domain,
                    "primary_color": colour,
                },
            )
            if created or not website.api_keys.filter(is_active=True).exists():
                issue_api_key(website=website, key_name="Demo widget key")
            websites.append(website)
        self.stdout.write(f"  {len(websites)} websites")
        return websites

    def _seed_destinations(self):
        destinations = {}
        for name, code, city, state in DESTINATIONS:
            destination, _ = Destination.objects.get_or_create(
                code=code,
                defaults={"name": name, "city": city, "state": state, "country": "India"},
            )
            destinations[code] = destination
        self.stdout.write(f"  {len(destinations)} destinations")
        return destinations

    def _seed_inventory(self, destinations):
        for name, code, stars, price, amenities in HOTELS:
            hotel, _ = Hotel.objects.get_or_create(
                name=name,
                defaults={
                    "destination": destinations[code],
                    "star_rating": stars,
                    "base_price": Decimal(price),
                    "amenities": amenities,
                    "description": f"A {stars}-star stay in {destinations[code].name}.",
                    "address": f"{destinations[code].city}, {destinations[code].state}",
                },
            )
            HotelOffer.objects.get_or_create(
                hotel=hotel,
                title="Early bird — 2 nights",
                defaults={
                    "room_type": "Deluxe",
                    "price": Decimal(price) * 2 * Decimal("0.9"),
                    "inclusions": "Breakfast for two, late checkout",
                },
            )

        for name, code, vehicle, brand, model, seats, price in CARS:
            CarRental.objects.get_or_create(
                name=name,
                defaults={
                    "destination": destinations[code],
                    "vehicle_type": vehicle,
                    "brand": brand,
                    "model_name": model,
                    "seats": seats,
                    "daily_price": Decimal(price),
                    "transmission": "Manual",
                    "fuel_type": "Petrol",
                    "description": f"{brand} {model} with driver, based in {destinations[code].name}.",
                },
            )

        for name, code, days, nights, price, inclusions in PACKAGES:
            TourPackage.objects.get_or_create(
                name=name,
                defaults={
                    "destination": destinations[code],
                    "duration_days": days,
                    "duration_nights": nights,
                    "base_price": Decimal(price),
                    "inclusions": inclusions,
                    "exclusions": "Flights, personal expenses",
                    "description": f"{days} days across {destinations[code].name}.",
                    "itinerary": [
                        {"day": index, "title": f"Day {index} in {destinations[code].name}"}
                        for index in range(1, days + 1)
                    ],
                },
            )
        self.stdout.write(
            f"  {Hotel.objects.count()} hotels, {CarRental.objects.count()} cars, "
            f"{TourPackage.objects.count()} packages"
        )

    def _seed_customers(self):
        customers = []
        for first, last, email, phone, city in CUSTOMERS:
            customer, _ = Customer.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "phone": phone,
                    "whatsapp": phone,
                    "city": city,
                },
            )
            customers.append(customer)
        self.stdout.write(f"  {len(customers)} customers")
        return customers

    def _seed_leads(self, customers, websites, staff):
        statuses = [
            LeadStatus.NEW,
            LeadStatus.QUALIFIED,
            LeadStatus.INTERESTED,
            LeadStatus.PAYMENT_PENDING,
            LeadStatus.CONVERTED,
            LeadStatus.FOLLOW_UP,
        ]
        agents = [staff["agent1"], staff["agent2"], staff["manager"]]
        sources = ["ai_chat", "website_form", "phone", "manual"]
        leads = []
        today = timezone.localdate()

        for index, customer in enumerate(customers):
            name, code, *_ = DESTINATIONS[index % len(DESTINATIONS)]
            if Lead.objects.filter(customer=customer).exists():
                leads.extend(Lead.objects.filter(customer=customer))
                continue
            lead = Lead(
                customer=customer,
                website=websites[index % len(websites)],
                title=f"{name} trip for {customer.first_name}",
                status=statuses[index % len(statuses)],
                source=sources[index % len(sources)],
                destination=name,
                travel_start=today + timedelta(days=20 + index * 5),
                travel_end=today + timedelta(days=25 + index * 5),
                travelers_count=random.choice([2, 2, 4, 6]),
                budget_min=Decimal(random.choice([15000, 25000, 40000])),
                budget_max=Decimal(random.choice([50000, 80000, 120000])),
                assigned_to=agents[index % len(agents)],
                preferences={"destination": name, "product_type": "package"},
            )
            crm_services.create_lead(lead=lead, actor=staff["manager"])
            crm_services.add_note(
                lead=lead,
                body=f"Customer asked about {name} — sent three options over WhatsApp.",
                author=lead.assigned_to,
            )
            leads.append(lead)

        # A couple of open follow-ups, one already overdue.
        now = timezone.now()
        for offset, lead in zip((-2, 1, 4), leads[:3]):
            if lead.follow_ups.exists():
                continue
            crm_services.create_follow_up(
                task=FollowUpTask(
                    lead=lead,
                    assigned_to=lead.assigned_to,
                    title=f"Call {lead.customer.first_name} about {lead.destination}",
                    due_at=now + timedelta(days=offset),
                    reminder_at=now + timedelta(days=offset, hours=-2),
                ),
                actor=staff["manager"],
            )
        self.stdout.write(f"  {len(leads)} leads")
        return leads

    def _seed_conversations(self, websites, customers):
        scripts = [
            "Hi",
            "I want a hotel in Goa for 4 people, budget 40000",
            "We are travelling 12/03/2027, can you share options?",
        ]
        if Conversation.objects.exists():
            self.stdout.write("  conversations already present, skipping")
            return
        for website in websites:
            conversation, _ = conversation_services.start_conversation(
                website=website, context={"source": website.source_identifier}
            )
            for line in scripts:
                conversation_services.handle_customer_message(
                    conversation=conversation, text=line
                )
        # One chat that asked for a human, so the inbox has something waiting.
        waiting, _ = conversation_services.start_conversation(website=websites[0])
        conversation_services.handle_customer_message(
            conversation=waiting, text="I want to speak to a human about Manali"
        )
        self.stdout.write(f"  {Conversation.objects.count()} conversations")

    def _seed_bookings(self, leads, staff):
        if Booking.objects.exists():
            self.stdout.write("  bookings already present, skipping")
            return
        packages = list(TourPackage.objects.all()[:3])
        today = timezone.localdate()
        for index, (lead, package) in enumerate(zip(leads, packages)):
            booking = Booking(
                website=lead.website,
                customer=lead.customer,
                lead=lead,
                product_type="package",
                product_id=package.pk,
                product_name=package.name,
                travel_start=today + timedelta(days=30 + index * 4),
                travel_end=today + timedelta(days=30 + index * 4 + package.duration_days),
                travelers_count=lead.travelers_count,
                subtotal=package.base_price,
                status=BookingStatus.PENDING,
            )
            booking_services.create_booking(booking=booking, actor=staff["agent1"])
            payment = booking_services.create_payment_order(
                booking=booking, actor=staff["agent1"]
            )
            # Settle the first one so the revenue reports have something in them.
            if index == 0:
                from bookings import payments as gateway

                payment_id, signature = gateway.simulate_payment(payment.razorpay_order_id)
                booking_services.verify_payment(
                    order_id=payment.razorpay_order_id,
                    payment_id=payment_id,
                    signature=signature,
                    actor=staff["agent1"],
                )
        self.stdout.write(f"  {Booking.objects.count()} bookings")
