from aiogram.fsm.state import State, StatesGroup


class CheckoutFlow(StatesGroup):
    waiting_custom_quantity = State()


class DepositFlow(StatesGroup):
    waiting_amount = State()
    waiting_method = State()


class TicketFlow(StatesGroup):
    waiting_message = State()


class AdminFlow(StatesGroup):
    add_product_name = State()
    add_product_emoji = State()
    add_product_price = State()
    add_product_unit = State()
    add_product_features = State()
    add_product_description = State()
    add_stock_pick_product = State()
    add_stock_paste = State()
    add_tier_pick_product = State()
    add_tier_qty = State()
    add_tier_price = State()
    add_coupon_code = State()
    add_coupon_kind = State()
    add_coupon_value = State()
    broadcast_text = State()
    set_price_pick_product = State()
    set_price_amount = State()
