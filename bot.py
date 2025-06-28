import shutil
import subprocess
import tempfile
from dotenv import load_dotenv
import os
import re
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from jinja2 import Template
from datetime import date, datetime

# Клавиатура подтверждения
CONFIRM_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Подтвердить"), KeyboardButton("Отменить")]],
    one_time_keyboard=True,
    resize_keyboard=True
)
# --- Константы анкеты ---
fields = [
    'customer_full_name',
    'student_name',
    'target',
    'email',
    'telegram',
    'passport_series_and_number',
    'passport_issue',
    'passport_issue_date',
    'passport_dept_code',
    'registration_address'
]

fields_answers = [
    'ФИО Заказчика',
    'ФИО Ученика',
    'Цель занятий',
    'Почта',
    'Телеграм',
    'Серия и номер паспорта',
    'Кем выдан',
    'Дата выдачи',
    'Код подразделения',
    'Адрес регистрации'
]

questions = [
    "Введите ФИО заказчика (Фамилия Имя Отчество):",
    "Введите ФИО ученика (ФИО полностью):",
    "К какому результату вы хотите прийти (цель занятий лучше описать подробнее):",
    "Введите e-mail (в формате username@example.com):",
    "Введите Ваш Telegram (в формате @username):",
    "Введите серию и номер паспорта (например, 1234 567890):",
    "Кем выдан паспорт (пример: ОВД района):",
    "Дата выдачи паспорта (ДД.ММ.ГГГГ):",
    "Код подразделения (например, 123-456):",
    "Адрес регистрации:",
]

# --- Валидации ---
def validate_full_name(value):
    normalized = ' '.join(value.strip().split())
    return bool(re.fullmatch(r'[А-Яа-яЁё]+ [А-Яа-яЁё]+ [А-Яа-яЁё]+', normalized))

def validate_email(value):
    return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", value.strip()))

def validate_telegram(value):
    return bool(re.match(r"^@[\w]{3,32}$", value.strip()))

def validate_passport_series_and_number(value):
    return bool(re.match(r"^\d{4}\s\d{6}$", value.strip()))

def validate_passport_issue_date(value):
    try:
        datetime.strptime(value.strip(), "%d.%m.%Y")
        return True
    except ValueError:
        return False

def validate_passport_dept_code(value):
    return bool(re.match(r"^\d{3}-\d{3}$", value.strip()))

def validate_nonempty(value):
    return len(value.strip()) > 0

validators = [
    validate_full_name,            # customer_full_name
    validate_full_name,            # student_name
    validate_nonempty,             # target
    validate_email,                # email
    validate_telegram,             # telegram
    validate_passport_series_and_number, # passport_series_and_number
    validate_nonempty,             # passport_issue
    validate_passport_issue_date,  # passport_issue_date
    validate_passport_dept_code,   # passport_dept_code
    validate_nonempty,             # registration_address
]

# --- Глобальные данные ---
load_dotenv()
API_TOKEN = os.getenv('API_KEY')
ADMIN_CHAT_ID = os.getenv('ADMIN_ID')
if not API_TOKEN:
    raise Exception("API_KEY не найден в .env файле!")
if not ADMIN_CHAT_ID:
    raise Exception("ADMIN_ID не найден в .env файле!")

user_data = {}         # user_id -> {'step': int, 'answers': {}, 'privacy_accepted': bool}
dogovor_count = 1      # Глобальный счетчик договоров


def compile_tex_with_latexmk(input_tex, output_pdf):
    """
    Компилирует input_tex в PDF с именем output_pdf (полный путь, с .pdf), используя latexmk и pdflatex.
    Возвращает путь к итоговому PDF либо None при ошибке.
    """
    input_dir = os.path.dirname(os.path.abspath(input_tex))
    input_base = os.path.basename(input_tex)

    out_basename = os.path.splitext(os.path.basename(output_pdf))[0]

    cmd = [
        "latexmk",
        "-pdf",
        f'-pdflatex=pdflatex -interaction=nonstopmode -jobname={out_basename} %O %S',
        input_base
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=input_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False  # не выбрасывать исключение при ошибке
        )
    except Exception as e:
        print(f"Ошибка запуска latexmk: {e}")
        return None

    output_pdf_path = os.path.join(input_dir, out_basename + ".pdf")

    if proc.returncode == 0 and os.path.exists(output_pdf_path):
        # Если путь назначения другой, переместить
        if os.path.abspath(output_pdf_path) != os.path.abspath(output_pdf):
            shutil.move(output_pdf_path, output_pdf)
        return output_pdf
    else:
        print("Ошибка компиляции latexmk!")
        print("STDOUT:\n", proc.stdout.decode(errors='ignore'))
        print("STDERR:\n", proc.stderr.decode(errors='ignore'))
        return None

def dogovor_create(answers):
    global dogovor_count
    try:
        with open('template.tex', encoding='utf-8') as f:
            tex_template = f.read()
    except Exception as e:
        print(f"Ошибка при открытии шаблона: {e}")
        return None

    current_date = date.today().strftime("%d.%m.%Y")
    try:
        split_passport = answers.get('passport_series_and_number', '').split()
        if len(split_passport) >= 2:
            passport_series = split_passport[0]
            passport_number = split_passport[1]
        else:
            passport_series = answers.get('passport_series_and_number', '')
            passport_number = ""
    except Exception:
        passport_series = answers.get('passport_series_and_number', '')
        passport_number = ""

    data = {
        'number_dogovor': f'{dogovor_count}/{"-".join(current_date.split(".")[1:])}',
        'data_dogovor': current_date,
        'customer_full_name': answers.get('customer_full_name', ''),
        'passport_series': passport_series,
        'passport_number': passport_number,
        'passport_issue': answers.get('passport_issue', ''),
        'passport_issue_date': answers.get('passport_issue_date', ''),
        'passport_dept_code': answers.get('passport_dept_code', ''),
        'registration_address': answers.get('registration_address', ''),
        'telegram': answers.get('telegram', '').replace('_', '\\_'),
        'email': answers.get('email', ''),
        'target': answers.get('target', ''),
        'student_name': answers.get('student_name', ''),
    }

    tex_result = Template(tex_template).render(**data)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_file_tex = os.path.join(tmpdir, 'dogovor.tex')
        output_file_pdf = os.path.join(tmpdir, 'dogovor.pdf')
        with open(output_file_tex, "w", encoding="utf-8") as f:
            f.write(tex_result)

        output = compile_tex_with_latexmk(output_file_tex, output_file_pdf)
        if output:
            final_output_file = os.path.abspath(f'dogovor_{dogovor_count}.pdf')
            shutil.copy2(output, final_output_file)
            dogovor_count += 1
            return final_output_file
        else:
            return None
# --- Хандлеры ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    # Сброс состояния
    user_data[user_id] = {'step': 0, 'answers': {}, 'privacy_accepted': False}# Показываем соглашение
    privacy_policy_url = "https://drive.google.com/file/d/1UUXMf6yP-9s6l2MGLBzqxFaK_Cy11DRo/view?usp=sharing"
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Даю согласие")]],
        one_time_keyboard=True,
        resize_keyboard=True
    )
    await update.message.reply_text(
        f"Ознакомьтесь, пожалуйста, с нашей [Политикой обработки персональных данных]({privacy_policy_url}).n"
        "Для продолжения необходимо Ваше согласие на обработку персональных данных в соответствии с Федеральным законом № 152-ФЗ \"О персональных данных\".\n"
        "Нажмите 'Даю согласие', чтобы продолжить.",
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    if user_id not in user_data:
        await update.message.reply_text("Пожалуйста, начните анкету командой /start.")
        return

    # Согласие на обработку персональных данных
    if user_data[user_id].get('privacy_accepted', False) is False:
        if text.lower() == "даю согласие":
            user_data[user_id]['privacy_accepted'] = True
            user_data[user_id]['step'] = 0
            await update.message.reply_text(
                questions[0],
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.message.reply_text(
                "Пожалуйста, подтвердите согласие на обработку данных, нажав кнопку \"Даю согласие\"."
            )
        return

    # Ожидание подтверждения/отмены после заполнения
    step = user_data[user_id].get('step', 0)
    if step >= len(fields):
        if text.lower() == "подтвердить":
            output_file = dogovor_create(user_data[user_id]['answers'])
            if output_file:
                try:
                    with open(output_file, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=ADMIN_CHAT_ID,
                            document=f,
                            filename=os.path.basename(output_file),
                            caption=f"{user_data[user_id]['answers'].get('email','')}\n{user_data[user_id]['answers'].get('telegram','')}",
                        )
                finally:
                    if os.path.exists(output_file):
                        os.remove(output_file)
            else:
                await update.message.reply_text(
                    "Произошла ошибка при формировании договора. Пожалуйста, попробуйте позже или обратитесь к администрации.",
                    reply_markup=ReplyKeyboardRemove()
                )
                user_data[user_id]['step'] = 0
                return

            await update.message.reply_text(
                "Ваш договор сформирован!\nОн придет в течение двух суток с этого момента на указанную вами почту. Его нужно будет подписать с помощью простой электронной подписи через сервис Контур Сайн, инструкция также будет приложена.",
                reply_markup=ReplyKeyboardRemove()
            )
            del user_data[user_id]
        elif text.lower() == "отменить":
            user_data[user_id] = {'step': 0, 'answers': {}, 'privacy_accepted': True}
            await update.message.reply_text(
                "Давайте попробуем еще раз. " + questions[0],
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.message.reply_text(
                "Пожалуйста, выберите 'Подтвердить' или 'Отменить'.",
                reply_markup=CONFIRM_KEYBOARD
            )
        return

    # Сбор данных по вопросам анкеты
    field = fields[step]
    validator = validators[step]
    if not validator(text):
        await update.message.reply_text(f"Некорректный ответ. Попробуйте еще раз!\n{questions[step]}")
        return

    user_data[user_id]['answers'][field] = text

    if step + 1 < len(questions):
        user_data[user_id]['step'] += 1
        await update.message.reply_text(questions[step + 1])
    else:
        # Анкета заполнена, предлагаем подтвердить
        user_data[user_id]['step'] += 1
        summary = "\n".join(
            [f"{fields_answers[i]}: {user_data[user_id]['answers'].get(fields[i],'')}" for i in range(len(fields_answers))]
        )
        await update.message.reply_text(
        f"Спасибо! Ваши ответы:\n{summary}"
        "\n\nЭти данные будут в настоящем договоре, в котором не должно быть ошибок!"
        "\n\nЕсли данные полностью корректны, то нажмите кнопку \"Подтвердить\", иначе \"Отменить\" и заполните данные ещё раз.",
        reply_markup=CONFIRM_KEYBOARD
        )

# --- main ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(API_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен!")
    app.run_polling()