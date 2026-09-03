from __future__ import annotations

from household_supply.domain.catalog import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
)
from household_supply.domain.items import Item, SKU
from household_supply.domain.quantity import Quantity
from household_supply.market.providers.globus_online import (
    GlobusOnlineListing,
)


_PROVIDER_ID = "globus-online-demo"


def _make_product(
    *,
    item_id: str,
    item_name: str,
    category: str,
    sku_id: str,
    sku_name: str,
    package_amount: str,
    package_unit: str,
    brand: str,
    url: str,
) -> tuple[Item, SKU, GlobusOnlineListing, CatalogBinding]:
    item = Item(
        id=item_id,
        canonical_name=item_name,
        category=category,
    )

    sku = SKU(
        id=sku_id,
        item=item,
        name=sku_name,
        package_quantity=Quantity(
            package_amount,
            package_unit,
        ),
        brand=brand,
    )

    listing = GlobusOnlineListing(url=url)

    binding = CatalogBinding(
        listing_key=ExternalListingKey(
            provider_id=_PROVIDER_ID,
            seller_id=listing.seller_id,
            external_product_id=listing.external_product_id,
        ),
        sku_id=sku.id,
        source=listing.url,
    )

    return item, sku, listing, binding


def build_globus_demo_staples_catalog() -> tuple[
    CatalogSnapshot,
    tuple[GlobusOnlineListing, ...],
]:
    products = (
        # Milk
        _make_product(
            item_id="milk",
            item_name="Молоко",
            category="dairy",
            sku_id="globus_milk_umut_1l",
            sku_name="Молоко Умут и К 3,2% 1000г т/п",
            package_amount="1",
            package_unit="l",
            brand="Умут и Ко",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "3b709086a89e4a1ab6c238ca5cf1a742000100010000"
            ),
        ),
        _make_product(
            item_id="milk",
            item_name="Молоко",
            category="dairy",
            sku_id="globus_milk_belaya_reka_1l",
            sku_name="Молоко Белая Река ультрапаст 2,5% 1л т/п",
            package_amount="1",
            package_unit="l",
            brand="Белая Река",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "38d9c8524241466b85a953c22c5b9d67000200010000"
            ),
        ),

        # Pasta
        _make_product(
            item_id="pasta",
            item_name="Макароны",
            category="pasta",
            sku_id="globus_pasta_sultan_400g",
            sku_name="Макароны Султан жгутики 400г",
            package_amount="400",
            package_unit="g",
            brand="Султан",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "e8cf6962d6374575bdb5ea9b85b0da3c000100010000"
            ),
        ),
        _make_product(
            item_id="pasta",
            item_name="Макароны",
            category="pasta",
            sku_id="globus_pasta_vkusvill_500g",
            sku_name="Макароны Вкусвилл Игрушки 500г",
            package_amount="500",
            package_unit="g",
            brand="Вкусвилл",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "60e1872ec8fa41708f08ffcb887b5d59000300010001"
            ),
        ),
        _make_product(
            item_id="pasta",
            item_name="Макароны",
            category="pasta",
            sku_id="globus_pasta_arbella_400g",
            sku_name="Макароны Arbella Спагетти 400г",
            package_amount="400",
            package_unit="g",
            brand="Arbella",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "17ee4601b478463abd42590942fe7bd8000200010000"
            ),
        ),

        # Sunflower oil
        _make_product(
            item_id="sunflower_oil",
            item_name="Подсолнечное масло",
            category="oil",
            sku_id="globus_oil_laska_1l",
            sku_name="Масло подсолнечное Ласка 1л РЦ",
            package_amount="1",
            package_unit="l",
            brand="Ласка",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "d620988254054bfb8380220c7a2984ea000100010000"
            ),
        ),
        _make_product(
            item_id="sunflower_oil",
            item_name="Подсолнечное масло",
            category="oil",
            sku_id="globus_oil_sloboda_1l",
            sku_name="Масло подсолнечное Слобода нерафин. 1л",
            package_amount="1",
            package_unit="l",
            brand="Слобода",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "ff90782f6d204899acd913be3cca2c7c000200010001"
            ),
        ),

        # Semolina
        _make_product(
            item_id="semolina",
            item_name="Манная крупа",
            category="groceries",
            sku_id="globus_semolina_sultan_650g",
            sku_name="Крупа манная Султан 650г",
            package_amount="650",
            package_unit="g",
            brand="Султан",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "8c8baac452b242dca3977824e605d892000100010000"
            ),
        ),

        # Canned food
        _make_product(
            item_id="canned_fish",
            item_name="Консервы рыбные",
            category="canned",
            sku_id="globus_sprats_shturval_160g",
            sku_name="Шпроты Штурвал в масле /ключ/ 160г ж/б Россия 36",
            package_amount="160",
            package_unit="g",
            brand="Штурвал",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "5c0c4fa88a7f4c0885cabd352fce8392000200010000"
            ),
        ),
        _make_product(
            item_id="canned_fish",
            item_name="Консервы рыбные",
            category="canned",
            sku_id="globus_sprats_shturval_240g",
            sku_name="Килька Штурвал балтийская в томат.соусе /ключ/ 240г ж/б Россия 48",
            package_amount="240",
            package_unit="g",
            brand="Штурвал",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "49bfd017e8484f10a1bb69d8e2b2928f000200010000"
            ),
        ),
        _make_product(
            item_id="canned_peas",
            item_name="Консервированный горошек",
            category="canned",
            sku_id="globus_peas_bonduelle_850ml",
            sku_name="Горошек Bonduelle конс. 850мл ж/б Россия 12",
            package_amount="850",
            package_unit="ml",
            brand="Bonduelle",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "92bdd9d4ca624013832a82d953ec8f35000300010001"
            ),
        ),

        # Seasonings
        _make_product(
            item_id="seasoning",
            item_name="Приправа",
            category="seasoning",
            sku_id="globus_seasoning_pripravych_75g",
            sku_name="Приправа Приправыч 12 овощей и трав 75г",
            package_amount="75",
            package_unit="g",
            brand="Приправыч",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "7d046b541839440e94632b4a8da84d06000200010000"
            ),
        ),
        _make_product(
            item_id="seasoning",
            item_name="Приправа",
            category="seasoning",
            sku_id="globus_seasoning_pripravych_200g",
            sku_name="Приправа Приправыч универс. пикантн. 200г д/п",
            package_amount="200",
            package_unit="g",
            brand="Приправыч",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "8486c96a9d66481ab7767702ceb2aaa3000200010001"
            ),
        ),
        _make_product(
            item_id="seasoning",
            item_name="Приправа",
            category="seasoning",
            sku_id="globus_seasoning_pripravych_60g",
            sku_name="Приправа Приправыч универс.100 блюд 60г д/п",
            package_amount="60",
            package_unit="g",
            brand="Приправыч",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "d0ef00e7e98e47f2879edae67b2f0ed4000100010000"
            ),
        ),

        # Eggs
        _make_product(
            item_id="eggs",
            item_name="Яйца",
            category="eggs",
            sku_id="globus_eggs_trit_10",
            sku_name="Яйцо куриное ТриТ Свежее 10шт С1",
            package_amount="10",
            package_unit="piece",
            brand="ТриТ",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "d4c8a670405d4306ba6b59fee8567614000200010000"
            ),
        ),
        _make_product(
            item_id="eggs",
            item_name="Яйца",
            category="eggs",
            sku_id="globus_eggs_zhar_ptitsa_10",
            sku_name="Яйцо куриное Жар Птица 10шт С1",
            package_amount="10",
            package_unit="piece",
            brand="Жар Птица",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "f484adc416d94937a82d8d237a7845ca000200010000"
            ),
        ),
        _make_product(
            item_id="eggs",
            item_name="Яйца",
            category="eggs",
            sku_id="globus_eggs_ak_kuu_30",
            sku_name="Яйцо Ак-Куу Наристе С2 30шт",
            package_amount="30",
            package_unit="piece",
            brand="Ак-Куу",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "f99413dd01bf4fab870519b28e799949000300010001"
            ),
        ),

        # Bread
        _make_product(
            item_id="bread",
            item_name="Хлеб",
            category="bread",
            sku_id="globus_bread_elita_350g",
            sku_name="Хлеб Элита Здоровое сердце 350г АЗО-Элита",
            package_amount="350",
            package_unit="g",
            brand="АЗО-Элита",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "8947a6e312b6419daa3b14c0c0d02d68000300010001"
            ),
        ),
        _make_product(
            item_id="bread",
            item_name="Хлеб",
            category="bread",
            sku_id="globus_bread_sp_gl_330g",
            sku_name="Хлеб пшеничный 330г СП GL",
            package_amount="330",
            package_unit="g",
            brand="СП GL",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "fc292a6f8038455abec2d57edf227408000200010000"
            ),
        ),
        _make_product(
            item_id="bread",
            item_name="Хлеб",
            category="bread",
            sku_id="globus_bread_elita_toast_600g",
            sku_name="Хлеб Элита Гранд- Элита тостерный 600г АЗО-ЭЛИТА",
            package_amount="600",
            package_unit="g",
            brand="АЗО-ЭЛИТА",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "9b4981e46b1a433fbd89d350265b1038000200010000"
            ),
        ),

        # Sugar
        _make_product(
            item_id="sugar",
            item_name="Сахар",
            category="sugar",
            sku_id="globus_sugar_master_kub_550g",
            sku_name="Сахар рафинад Мастер Куб 550г",
            package_amount="550",
            package_unit="g",
            brand="Мастер Куб",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "c0ad499dcf644aafabd3959c04195e4e000100010000"
            ),
        ),
        _make_product(
            item_id="sugar",
            item_name="Сахар",
            category="sugar",
            sku_id="globus_sugar_ak_kant_850g",
            sku_name="Сахар рафинад Ак-Кант 850г",
            package_amount="850",
            package_unit="g",
            brand="Ак-Кант",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "332c297fbdd74079a1e3a36420d24df7000100010000"
            ),
        ),
        _make_product(
            item_id="sugar",
            item_name="Сахар",
            category="sugar",
            sku_id="globus_sugar_crystal_stick_500g",
            sku_name="Сахар рафинад Crystal Stick 3D 500г",
            package_amount="500",
            package_unit="g",
            brand="Crystal",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "82c4833f00064ce7b531521ffb468dec000300010001"
            ),
        ),
    )

    skus = tuple(product[1] for product in products)
    listings = tuple(product[2] for product in products)
    bindings = tuple(product[3] for product in products)

    catalog = CatalogSnapshot(
        skus=skus,
        bindings=bindings,
    )

    return catalog, listings