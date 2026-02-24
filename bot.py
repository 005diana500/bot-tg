import json
from decimal import Decimal
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils import executor


import os

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Не задана переменная окружения BOT_TOKEN")
    
with open("tariffs.json", "r", encoding="utf-8") as f:
    TARIFFS = json.load(f)

bot = Bot(token=TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())



# ===================== STATES =====================

class Form(StatesGroup):
    sum_constr = State()
    sum_finish = State()
    material = State()
    risk_pech = State()
    risk_novoe = State()
    risk_gr1 = State()
    risk_gr23 = State()


# ===================== UTILITIES =====================

def parse_amount(text: str) -> Decimal:
    txt = text.lower().strip().replace(" ", "").replace(",", ".")
    multipliers = {
        "млн": Decimal("1000000"),
        "m": Decimal("1000000"),
        "тыс": Decimal("1000"),
        "k": Decimal("1000"),
    }
    for key, mult in multipliers.items():
        if key in txt:
            val = txt.replace(key, "")
            return Decimal(val) * mult
    return Decimal(txt)


def find_tariff(material: str, section: str, total_sum: Decimal) -> Decimal:
    data = TARIFFS[material.lower()][section]
    for item in data:
        min_v = Decimal(str(item["min"]))
        max_v = item["max"]
        max_v = Decimal(str(max_v)) if max_v is not None else None

        if (total_sum >= min_v) and (max_v is None or total_sum <= max_v):
            return Decimal(str(item["tariff"]))

    raise ValueError("Не найден тариф")


def calculate_premium(sum_constr, sum_finish, material, pech, novoe, gr1, gr23):
    S_constr = Decimal(sum_constr)
    S_finish = Decimal(sum_finish)
    S_total = S_constr + S_finish

    T_base_constr = find_tariff(material, "constructive", S_total)
    T_base_fin = find_tariff(material, "finishing", S_total)

    K_pech = Decimal("1.15") if pech else Decimal("1.00")
    K_novoe = Decimal("1.10") if novoe else Decimal("1.00")
    K_full = K_pech * K_novoe

    T_constr_final = T_base_constr * K_full
    T_fin_final = T_base_fin * K_full

    P_constr = (S_constr * T_constr_final / Decimal("100")).quantize(Decimal("0.01"))
    P_finish = (S_finish * T_fin_final / Decimal("100")).quantize(Decimal("0.01"))

    add_gr1 = Decimal("0.100") if gr1 else Decimal("0")
    add_gr23 = Decimal("0.060") if gr23 else Decimal("0")

    P_gr1 = (S_total * add_gr1 / Decimal("100")).quantize(Decimal("0.01"))
    P_gr23 = (S_total * add_gr23 / Decimal("100")).quantize(Decimal("0.01"))

    total = P_constr + P_finish + P_gr1 + P_gr23

    return T_constr_final, T_fin_final, P_constr, P_finish, P_gr1, P_gr23, total


# ===================== HANDLERS =====================

# Запуск с любого сообщения, если нет активного состояния
@dp.message_handler(state=None)
async def start_from_anything(message: types.Message, state: FSMContext):
    await Form.sum_constr.set()
    await message.answer("Привет! Введите сумму КОНСТРУКТИВА:")


# Команда "стоп" — полный перезапуск
@dp.message_handler(lambda message: message.text.lower() == "стоп", state="*")
async def stop_and_restart(message: types.Message, state: FSMContext):
    await state.finish()
    await Form.sum_constr.set()
    await message.answer("Привет! Введите сумму КОНСТРУКТИВА:")


# Команда СТОП — перезапуск расчёта
@dp.message_handler(lambda message: message.text.lower() == "стоп", state="*")
async def stop_and_restart(message: types.Message, state: FSMContext):
    await state.finish()
    await Form.sum_constr.set()
    await message.answer(
        "Расчёт сброшен.\nВведите сумму КОНСТРУКТИВА:",
        reply_markup=main_kb
    )


@dp.message_handler(state=Form.sum_constr)
async def get_constr(message: types.Message, state: FSMContext):
    try:
        s = parse_amount(message.text)
    except:
        await message.answer("Введите корректную сумму.")
        return

    await state.update_data(sum_constr=str(s))
    await Form.next()
    await message.answer("Введите сумму ОТДЕЛКИ:")


@dp.message_handler(state=Form.sum_finish)
async def get_finish(message: types.Message, state: FSMContext):
    try:
        s = parse_amount(message.text)
    except:
        await message.answer("Введите корректную сумму.")
        return

    await state.update_data(sum_finish=str(s))

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Дерево"), KeyboardButton("Камень"))

    await Form.next()
    await message.answer("Материал стен:", reply_markup=kb)


@dp.message_handler(state=Form.material)
async def get_material(message: types.Message, state: FSMContext):
    if message.text not in ["Дерево", "Камень"]:
        await message.answer("Выберите Дерево или Камень")
        return

    await state.update_data(material=message.text)

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Да"), KeyboardButton("Нет"))

    await Form.next()
    await message.answer("Есть печь/камин?", reply_markup=kb)


@dp.message_handler(state=Form.risk_pech)
async def get_pech(message: types.Message, state: FSMContext):
    await state.update_data(risk_pech=(message.text == "Да"))
    await Form.next()
    await message.answer("Применять 'новое за старое'?")


@dp.message_handler(state=Form.risk_novoe)
async def get_novoe(message: types.Message, state: FSMContext):
    await state.update_data(risk_novoe=(message.text == "Да"))
    await Form.next()
    await message.answer("Добавить ГР1?")


@dp.message_handler(state=Form.risk_gr1)
async def get_gr1(message: types.Message, state: FSMContext):
    await state.update_data(risk_gr1=(message.text == "Да"))
    await Form.next()
    await message.answer("Добавить ГР2+3?")


@dp.message_handler(state=Form.risk_gr23)
async def finish_calc(message: types.Message, state: FSMContext):
    await state.update_data(risk_gr23=(message.text == "Да"))
    data = await state.get_data()

    result = calculate_premium(
        data["sum_constr"],
        data["sum_finish"],
        data["material"],
        data["risk_pech"],
        data["risk_novoe"],
        data["risk_gr1"],
        data["risk_gr23"],
    )

    msg = f"""
📊 Расчёт премии

Конструктив: {data['sum_constr']}
Отделка: {data['sum_finish']}
Материал: {data['material']}

Тариф конструктив: {result[0]} %
Премия конструктив: {result[2]}

Тариф отделка: {result[1]} %
Премия отделка: {result[3]}

ГР1: {result[4]}
ГР2+3: {result[5]}

💰 ИТОГО: {result[6]}
"""

    await message.answer(msg)
    await state.finish()


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
