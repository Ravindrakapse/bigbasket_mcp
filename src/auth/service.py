import os
import time

from playwright.async_api import async_playwright

BASE_URL = "https://www.bigbasket.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BigBasketAuth:
    """Owns the Playwright browser and the persisted BigBasket session.

    Every API call in this server is issued from inside a real page context so
    that cookies, CSRF tokens and Akamai bot-manager telemetry are handled by
    the site's own JavaScript. Raw HTTP calls to these endpoints get blocked.
    """

    def __init__(self, headless: bool = True, session_path: str = None):
        self.headless = headless
        self.session_path = session_path or os.path.expanduser(
            "~/.bigbasket_mcp/cookies/auth.json"
        )
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._last_saved = 0.0

    async def _launch_browser(self):
        """Launch Chromium, tolerating partial Playwright browser installs.

        The `chromium` channel comes first on purpose. Playwright's default
        headless build is the stripped-down shell, which BigBasket's bot
        protection answers with 403; the full browser in new headless mode is
        served normally.
        """
        errors = []
        launch_args = ["--disable-blink-features=AutomationControlled"]
        for kwargs in (
            {"headless": self.headless, "channel": "chromium", "args": launch_args},
            {"headless": self.headless, "args": launch_args},
        ):
            try:
                return await self.playwright.chromium.launch(**kwargs)
            except Exception as e:
                errors.append(f"chromium {kwargs.get('channel', 'default')}: {e}")
        try:
            return await self.playwright.firefox.launch(headless=self.headless)
        except Exception as e:
            errors.append(f"firefox: {e}")
        raise RuntimeError(
            "Could not launch a browser. Run `playwright install chromium`.\n"
            + "\n".join(errors)
        )

    async def start_browser(self):
        from src.utils.geo import get_current_location

        self.playwright = await async_playwright().start()
        self.browser = await self._launch_browser()

        geolocation = get_current_location()
        print(f"Using delivery coordinates: {geolocation}")

        context_args = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": USER_AGENT,
            "locale": "en-IN",
            "timezone_id": "Asia/Kolkata",
            "geolocation": geolocation,
            "permissions": ["geolocation"],
        }
        if os.path.exists(self.session_path):
            print(f"Loading session from {self.session_path}")
            context_args["storage_state"] = self.session_path
        else:
            print("No saved session found. Starting a fresh one.")

        self.context = await self.browser.new_context(**context_args)
        self.page = await self.context.new_page()

        try:
            await self.page.goto(
                BASE_URL + "/", timeout=60000, wait_until="domcontentloaded"
            )
            print("Opened bigbasket.com")
        except Exception as e:
            print(f"Warning: loading bigbasket.com was slow or failed: {e}")

        await self.warm_up()

    async def warm_up(self, timeout_ms: int = 30000) -> bool:
        """Wait until BigBasket's bot protection has cleared this session.

        The site runs an Akamai sensor script that mints the `_abck` cookie a
        second or two after load. API calls made before that are answered with
        403, so poll a cheap endpoint until it succeeds.
        """
        waited = 0
        while waited < timeout_ms:
            await self.page.wait_for_timeout(1500)
            waited += 1500
            try:
                status = await self.page.evaluate(
                    """async () => {
                        const r = await fetch('/basketService/get/', {
                            headers: {'x-channel': 'BB-WEB', 'content-type': 'application/json'},
                            credentials: 'include',
                        });
                        return r.status;
                    }"""
                )
            except Exception:
                continue
            if status == 200:
                print(f"Session ready after {waited}ms.")
                return True

        print("Bot protection did not clear in time. Calls may return 403.")
        return False

    async def is_logged_in(self) -> bool:
        """A logged-in session can read the member address book; a guest gets 401."""
        if not self.page or self.page.is_closed():
            return False
        try:
            status = await self.page.evaluate(
                """async () => {
                    const r = await fetch('/ui-svc/v1/address/list', {
                        headers: {'x-channel': 'BB-WEB', 'content-type': 'application/json'},
                        credentials: 'include',
                    });
                    return r.status;
                }"""
            )
            if status == 200:
                return True
            if status in (401, 403):
                return False
        except Exception:
            pass
        try:
            body = await self.page.evaluate("() => document.body.innerText")
            return "Login/ Sign Up" not in body and "Login/Sign Up" not in body
        except Exception:
            return False

    async def login(self, phone_number: str):
        """Open the login modal and submit the phone number so BigBasket sends an OTP."""
        print(f"Starting login for {phone_number}...")
        try:
            await self.page.goto(
                BASE_URL + "/", timeout=60000, wait_until="domcontentloaded"
            )
            await self.page.wait_for_timeout(4000)
        except Exception as e:
            print(f"Could not reload the home page: {e}")

        clicked = False
        for selector in [
            "button:has-text('Login/ Sign Up')",
            "button:has-text('Login/Sign Up')",
            "button:has-text('Login')",
            "div:has-text('Login/ Sign Up')",
        ]:
            try:
                el = self.page.locator(selector).first
                if await el.count() and await el.is_visible():
                    await el.click(timeout=5000)
                    clicked = True
                    print(f"Opened the login modal via {selector}")
                    break
            except Exception:
                continue
        if not clicked:
            print("Could not find the Login button. The modal may already be open.")

        await self.page.wait_for_timeout(2500)

        try:
            # The modal's field is a plain text input named multiform that takes a
            # phone number or an email address.
            phone_input = await self.page.wait_for_selector(
                "input#multiform, input[name='multiform'], "
                "input[placeholder*='Phone number'], input[type='tel']",
                state="visible",
                timeout=20000,
            )
            await phone_input.click()
            await phone_input.fill(phone_number)
            await self.page.wait_for_timeout(800)

            for selector in [
                "button:has-text('Continue')",
                "button:has-text('Get OTP')",
                "button:has-text('Next')",
                "button[type='submit']",
            ]:
                btn = self.page.locator(selector).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=5000)
                    print(f"Submitted the phone number via {selector}")
                    break
            else:
                await self.page.keyboard.press("Enter")
                print("Submitted the phone number with Enter.")

            await self.page.wait_for_timeout(5000)
            body = await self.page.evaluate("() => document.body.innerText")
            if "OTP" in body or "otp" in body:
                print("BigBasket is asking for an OTP. Call enter_otp with the code.")
            else:
                print(
                    "No OTP prompt was detected. The number may be wrong, or a "
                    "captcha may be blocking the flow; run with HEADLESS=false to look."
                )
        except Exception as e:
            print(f"Could not submit the phone number: {e}")

    async def enter_otp(self, otp: str):
        """Fill the OTP boxes inside the login modal and submit."""
        try:
            scope = self.page.locator("[role='dialog'], [class*='Modal']").first
            if not await scope.count():
                scope = self.page

            # Ignore the header search boxes, which are also visible text inputs.
            boxes = scope.locator(
                "input:not([placeholder*='Search']):not([type='hidden'])"
            )
            await boxes.first.wait_for(state="visible", timeout=20000)

            visible = []
            for i in range(await boxes.count()):
                box = boxes.nth(i)
                try:
                    if await box.is_visible():
                        visible.append(box)
                except Exception:
                    continue

            if len(visible) >= len(otp) > 1:
                for box, digit in zip(visible[-len(otp):], otp):
                    await box.fill(digit)
                    await self.page.wait_for_timeout(150)
            elif visible:
                await visible[0].fill(otp)
            else:
                print("No OTP field was visible in the login modal.")
                return

            await self.page.wait_for_timeout(800)
            for selector in [
                "button:has-text('Continue')",
                "button:has-text('Verify')",
                "button:has-text('Submit')",
            ]:
                btn = self.page.locator(selector).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=5000)
                    break
            else:
                await self.page.keyboard.press("Enter")

            await self.page.wait_for_timeout(6000)
            print("OTP submitted.")
        except Exception as e:
            print(f"Could not enter the OTP: {e}")

    async def save_session(self, quiet: bool = False):
        os.makedirs(os.path.dirname(self.session_path), exist_ok=True)
        await self.context.storage_state(path=self.session_path)
        self._last_saved = time.monotonic()
        if not quiet:
            print(f"Session saved to {self.session_path}")

    async def maybe_save_session(self, min_interval: float = 60.0):
        """Persist cookies periodically so a login survives restarts.

        BigBasket rotates its session cookies as you browse, so the file on disk
        goes stale if it is only written at login time.
        """
        if not self.context:
            return
        if time.monotonic() - self._last_saved < min_interval:
            return
        try:
            await self.save_session(quiet=True)
        except Exception as e:
            print(f"Could not refresh the saved session: {e}")

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
