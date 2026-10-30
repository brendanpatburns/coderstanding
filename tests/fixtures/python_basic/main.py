from shop import cart
from shop.cart import Cart


def run():
    return cart.Cart.total(Cart(None))


if __name__ == "__main__":
    run()
