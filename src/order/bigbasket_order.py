from playwright.async_api import Page

from .services.cart import CartService
from .services.checkout import CheckoutService
from .services.location import LocationService
from .services.orders import OrderService
from .services.payment import PaymentService
from .services.product import ProductService
from .services.search import SearchService


class BigBasketOrder:
    """Facade over the catalogue, cart, location, order and payment services.

    It also keeps a per-session product cache, because BigBasket's cart endpoint
    returns only SKU ids and quantities.
    """

    def __init__(self, page: Page):
        self.page = page
        self.known_products: dict[str, dict] = {}

        self.search_service = SearchService(page, self)
        self.product_service = ProductService(page, self)
        self.cart_service = CartService(page, self)
        self.location_service = LocationService(page, self)
        self.checkout_service = CheckoutService(page, self)
        self.payment_service = PaymentService(page, self)
        self.order_service = OrderService(page, self)

    # Catalogue
    async def search_products(self, query: str, limit: int = 10, page: int = 1):
        return await self.search_service.search_products(query, limit, page)

    async def suggest(self, term: str):
        return await self.search_service.suggest(term)

    async def get_product_details(self, product_id: str):
        return await self.product_service.get_product_details(product_id)

    # Cart
    async def update_cart(self, cart_items: list, replace_cart: bool = False):
        return await self.cart_service.update_cart(cart_items, replace_cart)

    async def add_to_cart(self, product_id: str, quantity: int = 1):
        return await self.cart_service.add_to_cart(product_id, quantity)

    async def set_quantity(self, product_id: str, quantity: int):
        return await self.cart_service.set_quantity(product_id, quantity)

    async def remove_from_cart(self, product_id: str, quantity: int = 1):
        return await self.cart_service.remove_from_cart(product_id, quantity)

    async def clear_cart(self):
        return await self.cart_service.clear_cart()

    async def view_cart(self):
        return await self.cart_service.view_cart()

    # Location and addresses
    async def get_delivery_info(self):
        return await self.location_service.get_delivery_info()

    async def get_location_serviceability(self, latitude: float, longitude: float):
        return await self.location_service.get_location_serviceability(latitude, longitude)

    async def list_saved_addresses(self):
        return await self.location_service.get_saved_addresses()

    async def select_saved_address(self, address_id: str):
        return await self.location_service.select_address(address_id)

    async def set_location(self, query: str):
        return await self.location_service.set_location(query)

    # Checkout and payment
    async def open_checkout(self):
        return await self.checkout_service.open_checkout()

    async def list_slots(self):
        return await self.checkout_service.list_slots()

    async def get_payment_methods(self):
        return await self.payment_service.get_payment_methods()

    async def select_payment_method(self, method: str = "cod"):
        return await self.payment_service.select_payment_method(method)

    async def check_payment_status(self):
        return await self.payment_service.check_payment_status()

    async def place_order(self):
        return await self.checkout_service.place_order()

    async def screenshot(self, path: str = None):
        return await self.checkout_service.screenshot(path)

    # Orders
    async def list_order_history(self, limit: int = 10):
        return await self.order_service.list_order_history(limit)

    async def get_order_detail(self, order_id: str):
        return await self.order_service.get_order_detail(order_id)

    async def get_past_order_items(self, limit: int = 30):
        return await self.order_service.get_past_order_items(limit)
