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
                # Rice
        _make_product(
            item_id="rice",
            item_name="Рис",
            category="rice",
            sku_id="globus_rice_passim_500g",
            sku_name="Рис Пассим круглозерн.пак. 500г",
            package_amount="500",
            package_unit="g",
            brand="Пассим",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "cd7c9e84529f4d3a84c0fb1ab878980e000100010000"
            ),
        ),
        _make_product(
            item_id="rice",
            item_name="Рис",
            category="rice",
            sku_id="globus_rice_no1_pakistani_900g",
            sku_name="Рис №1 Пакистанский 900г PL1",
            package_amount="900",
            package_unit="g",
            brand="№1",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "2967cc423f384a57ac4a6ef7dceb0f7c000300010001"
            ),
        ),
        _make_product(
            item_id="rice",
            item_name="Рис",
            category="rice",
            sku_id="globus_rice_no1_krasnodar_400g",
            sku_name="Рис №1 Краснодарский круглозерн ТУ 400г PL1",
            package_amount="400",
            package_unit="g",
            brand="№1",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "921cc5ab5eec47c487c8f31fff8ee83d000200010001"
            ),
        ),
        _make_product(
            item_id="rice",
            item_name="Рис",
            category="rice",
            sku_id="globus_rice_no1_sechka_400g",
            sku_name="Рис №1 Сечка 400г PL1",
            package_amount="400",
            package_unit="g",
            brand="№1",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "e74f62750b784bdc847d6082c2e1ff31000100010000"
            ),
        ),

        # Buckwheat
        _make_product(
            item_id="buckwheat",
            item_name="Гречка",
            category="cereals",
            sku_id="globus_buckwheat_uvelka_800g",
            sku_name="Гречка Увелка Экстра быстроразвар. 800г",
            package_amount="800",
            package_unit="g",
            brand="Увелка",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "a4592fa805fe4d77acfd5be2d619d098000200010000"
            ),
        ),
        _make_product(
            item_id="buckwheat",
            item_name="Гречка",
            category="cereals",
            sku_id="globus_buckwheat_tsar_800g",
            sku_name="Гречка Царь 800г",
            package_amount="800",
            package_unit="g",
            brand="Царь",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "a3cc836537f847379b805095aaaef0e5000200010000"
            ),
        ),
        _make_product(
            item_id="buckwheat",
            item_name="Гречка",
            category="cereals",
            sku_id="globus_buckwheat_makfa_400g",
            sku_name="Гречка Макфа ядрица пак. 400г кор",
            package_amount="400",
            package_unit="g",
            brand="Макфа",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "ac51ea486a2749e7bb36620b0e460fa1000100010000"
            ),
        ),

        # Oatmeal
        _make_product(
            item_id="oatmeal",
            item_name="Овсянка",
            category="cereals",
            sku_id="globus_oatmeal_uvelka_400g",
            sku_name="Хлопья Увелка овсяные Геркулес 400г д/п",
            package_amount="400",
            package_unit="g",
            brand="Увелка",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "c78f8dc6f4954fc9b2708d95cd43b053000200010000"
            ),
        ),
        _make_product(
            item_id="oatmeal",
            item_name="Овсянка",
            category="cereals",
            sku_id="globus_oatmeal_makfa_350g",
            sku_name="Хлопья Макфа Овсяные 350г",
            package_amount="350",
            package_unit="g",
            brand="Макфа",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "5f7a55b637c54c70a1fb9acab8d7e5e7000300010001"
            ),
        ),
        _make_product(
            item_id="oatmeal",
            item_name="Овсянка",
            category="cereals",
            sku_id="globus_oatmeal_makfa_400g",
            sku_name="Хлопья Макфа овсяные 400г кор",
            package_amount="400",
            package_unit="g",
            brand="Макфа",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "13ab8b9f751c476b8afe817e87b976a3000200010001"
            ),
        ),

        # Flour
        _make_product(
            item_id="flour",
            item_name="Мука",
            category="flour",
            sku_id="globus_flour_astyk_2kg",
            sku_name="Мука Астык в/с 2кг",
            package_amount="2",
            package_unit="kg",
            brand="Астык",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "02a079571ae24028afbf493f5983426d000200010001"
            ),
        ),
        _make_product(
            item_id="flour",
            item_name="Мука",
            category="flour",
            sku_id="globus_flour_orion_1kg",
            sku_name="Мука Orion в/с 1кг",
            package_amount="1",
            package_unit="kg",
            brand="Orion",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "28b6ec6abf404513925269523ce338b8000300010001"
            ),
        ),
        _make_product(
            item_id="flour",
            item_name="Мука",
            category="flour",
            sku_id="globus_flour_ramenskaya_2kg",
            sku_name="Мука Раменская пшен хлебопекар в/с 2кг",
            package_amount="2",
            package_unit="kg",
            brand="Раменская",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "0db995db93154eddbec63a13e50db437000100010000"
            ),
        ),

        # Salt
        _make_product(
            item_id="salt",
            item_name="Соль",
            category="salt",
            sku_id="globus_salt_extra_1kg",
            sku_name="Соль Экстра пищевая йодиров. 1кг",
            package_amount="1",
            package_unit="kg",
            brand="Экстра",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "7450931f8f0a4b3eb09cbbe4db4a5c63000300010001"
            ),
        ),
        _make_product(
            item_id="salt",
            item_name="Соль",
            category="salt",
            sku_id="globus_salt_araltuz_1kg",
            sku_name="Соль пищевая Аралтуз 1 кг",
            package_amount="1",
            package_unit="kg",
            brand="Аралтуз",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "558ffe164bc345969f581c44e4d7430a000100010000"
            ),
        ),

                # Tea
        _make_product(
            item_id="tea_bags",
            item_name="Чай в пакетиках",
            category="tea",
            sku_id="globus_tea_beta_mint_lemon_25",
            sku_name="Чай Beta Mint & Lemon 25шт",
            package_amount="25",
            package_unit="piece",
            brand="Beta",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "b9ce1fb0f3ea46218b372010613aec90000300010001"
            ),
        ),
        _make_product(
            item_id="tea_loose",
            item_name="Чай листовой",
            category="tea",
            sku_id="globus_tea_tess_flame_90g",
            sku_name="Чай Tess Flame клубн трав 90г",
            package_amount="90",
            package_unit="g",
            brand="Tess",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "73ec099c392d4eac8f6f61d0cad889cb000100010000"
            ),
        ),
        _make_product(
            item_id="tea_bags",
            item_name="Чай в пакетиках",
            category="tea",
            sku_id="globus_tea_polezny_pohudin_25",
            sku_name="Чай Полезный Похудин 25шт",
            package_amount="25",
            package_unit="piece",
            brand="Полезный",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "69cb431cb8484a449d5c0f74d0217047000100010000"
            ),
        ),
        _make_product(
            item_id="tea_bags",
            item_name="Чай в пакетиках",
            category="tea",
            sku_id="globus_tea_greenfield_classic_breakfast_25",
            sku_name="Чай Greenfield Classic Breakfast 25шт",
            package_amount="25",
            package_unit="piece",
            brand="Greenfield",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "27d81f26ce6540979c4c9ca5598f546a000200010000"
            ),
        ),

        # Coffee
        _make_product(
            item_id="coffee",
            item_name="Кофе",
            category="coffee",
            sku_id="globus_coffee_nescafe_gold_85g",
            sku_name="Кофе Nescafe Gold Alta Rica раств 85г ст/б",
            package_amount="85",
            package_unit="g",
            brand="Nescafe",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "19720e5383334b5b9b72e637716b70e3000300010001"
            ),
        ),
        _make_product(
            item_id="coffee",
            item_name="Кофе",
            category="coffee",
            sku_id="globus_coffee_jockey_vostochnyi_250g",
            sku_name="Кофе Жокей По-восточному молот. 250г в/у",
            package_amount="250",
            package_unit="g",
            brand="Жокей",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "7324d34013424a7fbe11a598585d562b000300010001"
            ),
        ),
        _make_product(
            item_id="coffee",
            item_name="Кофе",
            category="coffee",
            sku_id="globus_coffee_jacobs_monarch_230g",
            sku_name="Кофе Jacobs Monarch в зёрнах 230г д/п",
            package_amount="230",
            package_unit="g",
            brand="Jacobs",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "38b8105ae92244d287da7f363c1e161b000200010001"
            ),
        ),

        # Water
        _make_product(
            item_id="water",
            item_name="Вода",
            category="water",
            sku_id="globus_water_corona_ice_10l",
            sku_name="Вода д/кулера Corona Ice 10л Кыргызстан",
            package_amount="10",
            package_unit="l",
            brand="Corona Ice",
            url=(
                "https://globus-online.kg/ru-kg/good/"
                "1a78edd20bfb4b9bb8e24105874b82f0000200010000"
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