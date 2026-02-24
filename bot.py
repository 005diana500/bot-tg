import os
import json
import asyncio
from decimal import Decimal, ROUND_HALF_UP

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.utils.keyboard import ReplyKeyboardBuilder


# ============================================================
#                 ЗАГРУЗКА БОТА И ТАРИФОВ
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("Установи переменную окружения TELEGRAM_TOKEN")

with open("tariffs.json", "r", encoding="utf-8") as f:
    TARIFFS = json.load(f)

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ============================================================
#                        STATES
# ============================================================

class Form(StatesGroup):
    sum_constr = State()
    sum_finish = State()
    material = State()
    risk_pech = State()
    risk_novoe = State()
    risk_gr1 = State()
    risk_gr23 = State()


# ============================================================
#             УТИЛИТЫ: ПАРСИНГ СУММ И ПОИСК ТАРИФОВ
# ============================================================

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
    """
    material: "дерево" или "камень"
    section: "constructive" или "finishing"
    total_sum: сумма конструктив + отделка → по ней определяем тариф
    """
    data = TARIFFS[material.lower()][section]
    for item in data:
        min_v = Decimal(str(item["min"]))
        max_v = item["max"]
        max_v = Decimal(str(max_v)) if max_v is not None else None

        if (total_sum >= min_v) and (max_v is None or total_sum <= max_v):
            return Decimal(str(item["tariff"]))

    raise ValueError("Не найден тариф для суммы " + str(total_sum))


# ============================================================
#                     РАСЧЁТ ПРЕМИИ
# ============================================================

def calculate_premium(sum_constr, sum_finish, material, pech, novoe, gr1, gr23):
    S_constr = Decimal(sum_constr)
    S_finish = Decimal(sum_finish)
    S_total = S_constr + S_finish

    # 1. Базовые тарифы (конструктив и отделка — по общей сумме!)
    T_base_constr = find_tariff(material, "constructive", S_total)
    T_base_fin = find_tariff(material, "finishing", S_total)

    # 2. Умножающие коэффициенты
    K_pech = Decimal("1.15") if pech else Decimal("1.00")
    K_novoe = Decimal("1.10") if novoe else Decimal("1.00")
    K_full = K_pech * K_novoe

    # 3. Итоговые тарифы
    T_constr_final = T_base_constr * K_full           # процент
    T_fin_final = T_base_fin * K_full                 # процент

    # 4. Премии
    P_constr = (S_constr * T_constr_final / Decimal("100")).quantize(Decimal("0.01"))
    P_finish = (S_finish * T_fin_final / Decimal("100")).quantize(Decimal("0.01"))

    # 5. ГР-добавки (отдельные премии, НЕ множители)
    add_gr1 = Decimal("0.100") if gr1 else Decimal("0")
    add_gr23 = Decimal("0.060") if gr23 else Decimal("0")

    P_gr1 = ((S_constr + S_finish) * add_gr1 / Decimal("100")).quantize(Decimal("0.01"))
    P_gr23 = ((S_constr + S_finish) * add_gr23 / Decimal("100")).quantize(Decimal("0.01"))

    total = P_constr + P_finish + P_gr1 + P_gr23

    return {
        "T_constr_final": T_constr_final,
        "T_fin_final": T_fin_final,
        "P_constr": P_constr,
        "P_finish": P_finish,
        "P_gr1": P_gr1,
        "P_gr23": P_gr23,
        "total": total
    }


# ============================================================
#                 HANDLERS — ВОПРОСЫ БОТА
# ============================================================

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я рассчитаю премию по новой формуле.\n"
        "Сначала укажи сумму КОНСТРУКТИВА:"
    )
    await state.set_state(Form.sum_constr)


@dp.message(StateFilter(Form.sum_constr))
async def get_constr(message: types.Message, state: FSMContext):
    try:
        s = parse_amount(message.text)
    except:
        await message.answer("Введите корректную сумму. Например: 2 млн / 350 тыс / 2500000")
        return

    await state.update_data(sum_constr=str(s))
    await message.answer("Теперь укажи сумму ОТДЕЛКИ:")
    await state.set_state(Form.sum_finish)


@dp.message(StateFilter(Form.sum_finish))
async def get_finish(message: types.Message, state: FSMContext):
    try:
        s = parse_amount(message.text)
    except:
        await message.answer("Введите корректную сумму отделки.")
        return

    await state.update_data(sum_finish=str(s))

    kb = ReplyKeyboardBuilder()
    kb.button(text="Дерево")
    kb.button(text="Камень")

    await message.answer("Материал стен дома:", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(Form.material)


@dp.message(StateFilter(Form.material))
async def get_material(message: types.Message, state: FSMContext):
    if message.text not in ["Дерево", "Камень"]:
        await message.answer("Выберите: Дерево или Камень")
        return

    await state.update_data(material=message.text)

    kb = ReplyKeyboardBuilder()
    kb.button(text="Да")
    kb.button(text="Нет")

    await message.answer("Есть печь/камин?", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(Form.risk_pech)


@dp.message(StateFilter(Form.risk_pech))
async def get_pech(message: types.Message, state: FSMContext):
    if message.text not in ["Да", "Нет"]:
        await message.answer("Ответьте: Да / Нет")
        return

    await state.update_data(risk_pech=(message.text == "Да"))

    kb = ReplyKeyboardBuilder()
    kb.button(text="Да")
    kb.button(text="Нет")
    await message.answer("Применять 'новое за старое'?", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(Form.risk_novoe)


@dp.message(StateFilter(Form.risk_novoe))
async def get_novoe(message: types.Message, state: FSMContext):
    if message.text not in ["Да", "Нет"]:
        await message.answer("Ответьте: Да / Нет")
        return

    await state.update_data(risk_novoe=(message.text == "Да"))

    kb = ReplyKeyboardBuilder()
    kb.button(text="Да")
    kb.button(text="Нет")
    await message.answer("Добавить ГР1?", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(Form.risk_gr1)


@dp.message(StateFilter(Form.risk_gr1))
async def get_gr1(message: types.Message, state: FSMContext):
    if message.text not in ["Да", "Нет"]:
        await message.answer("Ответьте: Да / Нет")
        return

    await state.update_data(risk_gr1=(message.text == "Да"))

    kb = ReplyKeyboardBuilder()
    kb.button(text="Да")
    kb.button(text="Нет")
    await message.answer("Добавить ГР2+3?", reply_markup=kb.as_markup(resize_keyboard=True))
    await state.set_state(Form.risk_gr23)


@dp.message(StateFilter(Form.risk_gr23))
async def finish_calc(message: types.Message, state: FSMContext):
    if message.text not in ["Да", "Нет"]:
        await message.answer("Ответьте: Да / Нет")
        return

    await state.update_data(risk_gr23=(message.text == "Да"))
    data = await state.get_data()

    result = calculate_premium(
        sum_constr=data["sum_constr"],
        sum_finish=data["sum_finish"],
        material=data["material"],
        pech=data["risk_pech"],
        novoe=data["risk_novoe"],
        gr1=data["risk_gr1"],
        gr23=data["risk_gr23"]
    )

    msg = (
        f"📊 *Расчёт премии*\n\n"
        f"🏠 Конструктив: {data['sum_constr']} руб.\n"
        f"🎨 Отделка: {data['sum_finish']} руб.\n"
        f"Материал: {data['material']}\n\n"

        f"Тариф конструктив: {result['T_constr_final']} %\n"
        f"Премия конструктив: {result['P_constr']} руб.\n\n"

        f"Тариф отделка: {result['T_fin_final']} %\n"
        f"Премия отделка: {result['P_finish']} руб.\n\n"

        f"ГР1: {result['P_gr1']} руб.\n"
        f"ГР2+3: {result['P_gr23']} руб.\n\n"

        f"💰 *ИТОГО:* {result['total']} руб."
    )

    await message.answer(msg, parse_mode="Markdown")
    await state.clear()
    await message.answer("Чтобы рассчитать снова — /start")


# ============================================================
#                     START POLLING
# ============================================================

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
