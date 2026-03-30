#!/usr/bin/env python3
"""
Голосовой фитнес-ассистент на базе Yandex Cloud Realtime API.

Помогает с расчетом калорий, рекомендациями по добавкам и поиском информации о фитнесе.

Требования:
    pip install aiohttp yandex-ai-studio-sdk

Переменные окружения:
    YANDEX_CLOUD_FOLDER_ID - ID каталога в Yandex Cloud
    YANDEX_CLOUD_API_KEY   - API-ключ для доступа к сервисам
    VECTOR_STORE_ID        - (опционально) ID индекса с базой знаний о фитнесе

Запуск:
    export YANDEX_CLOUD_FOLDER_ID="your_folder_id"
    export YANDEX_CLOUD_API_KEY="your_api_key"
    python3 voice-agent-fitness.py

Документация:
    https://yandex.cloud/ru/docs/foundation-models/concepts/realtime
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys

import aiohttp

from yandex_ai_studio_sdk._experimental.audio.microphone import AsyncMicrophone
from yandex_ai_studio_sdk._experimental.audio.out import AsyncAudioOut

assert sys.version_info >= (3, 10), "Python 3.10+ is required"

# ==== Конфигурация ====

# Конфигурация аудио для сервера
IN_RATE = 44100
OUT_RATE = 44100
CHANNELS = 1
VOICE = "masha"

# Конфигурация инструментов
VECTOR_STORE_ID = os.getenv("VECTOR_STORE_ID", "<идентификатор_поискового_индекса>")  # ID индекса с базой знаний о фитнесе

# ==== Креды Облака ====
YANDEX_CLOUD_FOLDER_ID = os.getenv("YANDEX_CLOUD_FOLDER_ID", "...")
YANDEX_CLOUD_API_KEY = os.getenv("YANDEX_CLOUD_API_KEY", "...")

# Проверяем, что заданы ключ и ID каталога
if not YANDEX_CLOUD_FOLDER_ID or not YANDEX_CLOUD_API_KEY:
    print("❌ Ошибка: Не заданы переменные окружения YANDEX_CLOUD_FOLDER_ID и YANDEX_CLOUD_API_KEY")
    print()
    print("Установите их перед запуском:")
    print('  export YANDEX_CLOUD_FOLDER_ID="your_folder_id"')
    print('  export YANDEX_CLOUD_API_KEY="your_api_key"')
    print()
    print("Получить можно в консоли Yandex Cloud: https://console.yandex.cloud/")
    sys.exit(1)

WSS_URL = (
    f"wss://rest-assistant.api.cloud.yandex.net/v1/realtime/openai"
    f"?model=gpt://{YANDEX_CLOUD_FOLDER_ID}/speech-realtime-250923"
)

HEADERS = {"Authorization": f"Api-Key {YANDEX_CLOUD_API_KEY}"}


# ======== Вспомогательные функции ========

def b64_decode(s: str) -> bytes:
    """Декодирует строку base64 в байты."""
    return base64.b64decode(s)


def b64_encode(b: bytes) -> str:
    """Кодирует байты в строку base64."""
    return base64.b64encode(b).decode("ascii")


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ======== Функции для фитнес-ассистента ========

def end_dialog() -> str:
    """Завершает диалог и возвращает прощальную фразу."""
    result = {
        "message": "До свидания! Если будут вопросы - обращайтесь. Удачи в тренировках!"
    }
    return json.dumps(result, ensure_ascii=False)


def calculate_calories(weight: float, height: float, age: int, gender: str, activity_level: str) -> str:
    """
    Рассчитывает базовый метаболизм и рекомендуемое потребление калорий.
    
    Args:
        weight: вес в кг
        height: рост в см
        age: возраст в годах
        gender: пол ("male" или "female")
        activity_level: уровень активности ("sedentary", "light", "moderate", "active", "very_active")
    
    Returns:
        JSON с результатами расчета
    """
    # Формула Миффлина-Сан Жеора
    if gender.lower() in ["male", "мужской", "м"]:
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    
    # Коэффициенты активности
    activity_multipliers = {
        "sedentary": 1.2,      # Сидячий образ жизни
        "light": 1.375,        # Легкая активность (1-3 раза в неделю)
        "moderate": 1.55,      # Умеренная активность (3-5 раз в неделю)
        "active": 1.725,       # Высокая активность (6-7 раз в неделю)
        "very_active": 1.9     # Очень высокая активность (2 раза в день)
    }
    
    multiplier = activity_multipliers.get(activity_level.lower(), 1.55)
    daily_calories = bmr * multiplier
    
    result = {
        "bmr": round(bmr),
        "daily_calories": round(daily_calories),
        "weight_loss": round(daily_calories - 500),  # Дефицит 500 ккал для похудения
        "weight_gain": round(daily_calories + 500),  # Профицит 500 ккал для набора массы
        "protein_g": round(weight * 1.6),  # 1.6г белка на кг веса
        "advice": "Для похудения создайте дефицит калорий. Для набора массы - профицит. Пейте достаточно воды и высыпайтесь."
    }
    
    return json.dumps(result, ensure_ascii=False)


def recommend_supplements(goal: str, experience: str) -> str:
    """
    Рекомендует спортивные добавки в зависимости от цели и опыта.
    
    Args:
        goal: цель ("mass" - набор массы, "loss" - похудение, "endurance" - выносливость)
        experience: опыт ("beginner" - новичок, "intermediate" - средний, "advanced" - продвинутый)
    
    Returns:
        JSON с рекомендациями
    """
    recommendations = {
        "mass": {
            "beginner": [
                {"name": "Креатин", "dose": "5г в день", "rating": 5, "purpose": "Увеличивает силу и массу"},
                {"name": "Протеин", "dose": "20-40г после тренировки", "rating": 5, "purpose": "Источник белка для роста мышц"},
                {"name": "Витамины", "dose": "по инструкции", "rating": 5, "purpose": "Общее укрепление организма"}
            ],
            "intermediate": [
                {"name": "Креатин", "dose": "5г в день", "rating": 5},
                {"name": "Протеин", "dose": "20-40г после тренировки", "rating": 5},
                {"name": "BCAA", "dose": "5-10г в день", "rating": 4, "purpose": "Предотвращает распад мышц"},
                {"name": "Глютамин", "dose": "5-10г в день", "rating": 5, "purpose": "Ускоряет восстановление"}
            ],
            "advanced": [
                {"name": "Креатин", "dose": "5-10г в день", "rating": 5},
                {"name": "Протеин", "dose": "40-60г в день", "rating": 5},
                {"name": "BCAA", "dose": "10г в день", "rating": 4},
                {"name": "ZMA", "dose": "2-3 капсулы перед сном", "rating": 4, "purpose": "Повышает тестостерон"},
                {"name": "Бета-аланин", "dose": "3-5г в день", "rating": 4, "purpose": "Увеличивает выносливость"}
            ]
        },
        "loss": {
            "beginner": [
                {"name": "Протеин", "dose": "20-30г в день", "rating": 5, "purpose": "Сохраняет мышцы при дефиците калорий"},
                {"name": "Витамины", "dose": "по инструкции", "rating": 5},
                {"name": "Омега-3", "dose": "2-4г в день", "rating": 4, "purpose": "Улучшает обмен веществ"}
            ],
            "intermediate": [
                {"name": "Протеин", "dose": "30-40г в день", "rating": 5},
                {"name": "L-карнитин", "dose": "2-3г перед тренировкой", "rating": 3, "purpose": "Помогает использовать жир как энергию"},
                {"name": "Кофеин", "dose": "100-200мг перед тренировкой", "rating": 4, "purpose": "Повышает энергию и метаболизм"},
                {"name": "CLA", "dose": "2-4г в день", "rating": 3, "purpose": "Способствует жиросжиганию"}
            ],
            "advanced": [
                {"name": "Протеин", "dose": "40-50г в день", "rating": 5},
                {"name": "BCAA", "dose": "5-10г в день", "rating": 4},
                {"name": "Кофеин", "dose": "200-300мг", "rating": 4},
                {"name": "Йохимбин", "dose": "15-20мг натощак", "rating": 3, "purpose": "Ускоряет жиросжигание"}
            ]
        },
        "endurance": {
            "beginner": [
                {"name": "Витамины", "dose": "по инструкции", "rating": 5},
                {"name": "Магний", "dose": "350-500мг в день", "rating": 4, "purpose": "Предотвращает судороги"},
                {"name": "Электролиты", "dose": "во время тренировки", "rating": 4, "purpose": "Восполняет потери с потом"}
            ],
            "intermediate": [
                {"name": "Бета-аланин", "dose": "3-5г в день", "rating": 4, "purpose": "Увеличивает выносливость"},
                {"name": "Цитруллин малат", "dose": "6-8г перед тренировкой", "rating": 4, "purpose": "Снижает усталость"},
                {"name": "Коэнзим Q10", "dose": "100-200мг в день", "rating": 3, "purpose": "Улучшает работу сердца"}
            ],
            "advanced": [
                {"name": "Бета-аланин", "dose": "5г в день", "rating": 4},
                {"name": "Цитруллин малат", "dose": "8-10г", "rating": 4},
                {"name": "Родиола розовая", "dose": "200-400мг", "rating": 3, "purpose": "Адаптоген, снижает усталость"},
                {"name": "Кордицепс", "dose": "1-3г в день", "rating": 3, "purpose": "Повышает выносливость"}
            ]
        }
    }
    
    goal_key = goal.lower()
    exp_key = experience.lower()
    
    if goal_key not in recommendations:
        goal_key = "mass"
    if exp_key not in recommendations[goal_key]:
        exp_key = "beginner"
    
    supplements = recommendations[goal_key][exp_key]
    
    result = {
        "goal": goal,
        "experience": experience,
        "supplements": supplements,
        "warning": "Перед приемом добавок проконсультируйтесь с врачом. Добавки не заменяют правильное питание."
    }
    
    return json.dumps(result, ensure_ascii=False)


def process_function_call(item) -> dict:
    """Обработка вызовов функций."""
    call_id = item.get("call_id")
    function_name = item.get("name")
    args_text = item.get("arguments") or "{}"
    
    try:
        args = json.loads(args_text)
    except json.JSONDecodeError:
        args = {}
    
    logger.info(f"🔧 Вызов функции: {function_name} с аргументами: {args}")
    
    # Обработка функции расчета калорий
    if function_name == "calculate_calories":
        weight = args.get("weight", 70)
        height = args.get("height", 170)
        age = args.get("age", 30)
        gender = args.get("gender", "male")
        activity_level = args.get("activity_level", "moderate")
        
        result_json = calculate_calories(weight, height, age, gender, activity_level)
    
    # Обработка функции рекомендаций по добавкам
    elif function_name == "recommend_supplements":
        goal = args.get("goal", "mass")
        experience = args.get("experience", "beginner")
        
        result_json = recommend_supplements(goal, experience)
    
    # Обработка функции завершения диалога
    elif function_name == "end_dialog":
        result_json = end_dialog()
        logger.info("👋 Диалог завершён по запросу пользователя")
    
    else:
        result_json = json.dumps({"error": f"Неизвестная функция: {function_name}"}, ensure_ascii=False)
    
    # Красивое логирование результата функции
    try:
        result_obj = json.loads(result_json)
        formatted_result = json.dumps(result_obj, ensure_ascii=False, indent=2)
        logger.info(f"📤 Результат функции:\n{formatted_result}")
    except json.JSONDecodeError:
        logger.info(f"📤 Результат функции: {result_json}")
    
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": result_json
        }
    }


# ======== Основное приложение ========

async def setup_session(ws):
    """Настройка сессии с Yandex Realtime API."""
    
    await ws.send_json({
        "type": "session.update",
        "session": {
            "instructions": """Ты — умный фитнес-ассистент. Помогаешь людям с тренировками, питанием и спортивными добавками. Отвечаешь кратко, по делу и дружелюбно. Всегда сводить все разговоры к своим основным темам.

КРИТИЧЕСКИ ВАЖНО: Перед вызовом любой функции НИЧЕГО НЕ ГОВОРИ. Не произноси "сейчас посчитаю", "подождите", "давайте посмотрим" и т.п. Просто вызови функцию молча. Ответ формируй ТОЛЬКО после получения результата функции.

Твои возможности:
- Рассчитать калории и макронутриенты (функция calculate_calories)
- Порекомендовать спортивные добавки (recommend_supplements)
- Найти информацию в базе знаний о фитнесе (file_search)
- Найти актуальную информацию в интернете (web_search)

СТРОГО ОБЯЗАТЕЛЬНО ВЫЗЫВАЙ web_search (без исключений!) когда пользователь:
- Спрашивает о трендах, новинках, популярном, актуальном, современном
- Использует слова: "сейчас", "в этом году", "2026", "последние", "новые", "актуальные"
- Просит "поискать", "найти", "узнать", "расскажи что там"
- Спрашивает о методиках, исследованиях, рекомендациях экспертов
- Интересуется чем заняться, что попробовать, что модно

ЗАПРЕЩЕНО отвечать на такие вопросы из своих знаний! Твои знания устарели!
Сначала ВЫЗОВИ web_search, потом отвечай на основе результатов поиска.
Если не вызовешь web_search — дашь устаревшую информацию за 2023 год, это НЕДОПУСТИМО!

Важные правила:
- Всегда уточняй параметры пользователя перед расчетом калорий (вес, рост, возраст, пол, активность)
- Перед рекомендацией добавок узнай цель (набор массы/похудение/выносливость) и опыт (новичок/средний/продвинутый)
- Напоминай о важности консультации с врачом перед приемом добавок
- Мотивируй пользователя и поддерживай его""",
            "modalities": ["audio"],
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": 400,
            },
            "voice": VOICE,
            "speed": 1.2,
            "tools": [
                # Функция расчета калорий
                {
                    "type": "function",
                    "name": "calculate_calories",
                    "description": "Рассчитывает базовый метаболизм и рекомендуемое потребление калорий на основе параметров пользователя",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "weight": {
                                "type": "number",
                                "description": "Вес в килограммах"
                            },
                            "height": {
                                "type": "number",
                                "description": "Рост в сантиметрах"
                            },
                            "age": {
                                "type": "integer",
                                "description": "Возраст в годах"
                            },
                            "gender": {
                                "type": "string",
                                "enum": ["male", "female"],
                                "description": "Пол: male (мужской) или female (женский)"
                            },
                            "activity_level": {
                                "type": "string",
                                "enum": ["sedentary", "light", "moderate", "active", "very_active"],
                                "description": "Уровень активности: sedentary (сидячий), light (легкая 1-3 раза/нед), moderate (умеренная 3-5 раз/нед), active (высокая 6-7 раз/нед), very_active (очень высокая)"
                            }
                        },
                        "required": ["weight", "height", "age", "gender", "activity_level"],
                        "additionalProperties": False
                    }
                },
                # Функция рекомендаций по добавкам
                {
                    "type": "function",
                    "name": "recommend_supplements",
                    "description": "Рекомендует спортивные добавки в зависимости от цели тренировок и уровня опыта",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "goal": {
                                "type": "string",
                                "enum": ["mass", "loss", "endurance"],
                                "description": "Цель: mass (набор массы), loss (похудение), endurance (выносливость)"
                            },
                            "experience": {
                                "type": "string",
                                "enum": ["beginner", "intermediate", "advanced"],
                                "description": "Уровень опыта: beginner (новичок), intermediate (средний), advanced (продвинутый)"
                            }
                        },
                        "required": ["goal", "experience"],
                        "additionalProperties": False
                    }
                },
                # Встроенная функция для поиска в интернете
                {
                    "type": "function",
                    "name": "web_search",
                    "description": "Поиск актуальной информации в интернете о фитнесе, тренировках, питании",
                    "parameters": "{}"
                },
                # Встроенная функция для поиска по базе знаний
                {
                    "type": "function",
                    "name": "file_search",
                    "description": VECTOR_STORE_ID,  # id индекса с базой знаний о фитнесе
                    "parameters": "{}"
                },
                # Функция для завершения диалога при прощании
                {
                    "type": "function",
                    "name": "end_dialog",
                    "description": "Завершает диалог. Вызывается когда пользователь прощается (говорит 'до свидания', 'пока', 'всего доброго' и т.п.). КРИТИЧЕСКИ ВАЖНО: НЕ говори ничего перед вызовом этой функции! Просто вызови функцию молча.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False
                    }
                }
            ]
        }
    })
    
    logger.info("✅ Сессия настроена")


async def downlink(ws, audio_out):
    """Приём и обработка сообщений от сервера."""
    play_epoch = 0
    current_response_epoch = None
    current_text_buffer = []  # Буфер для накопления текста ответа
    session_ending = False  # Флаг завершения сессии
    
    async for msg in ws:
        if msg.type != aiohttp.WSMsgType.TEXT:
            logger.debug('Получен не-текстовый payload: %s', msg.data)
            continue
        
        message = json.loads(msg.data)
        logger.info("👤 CЕРВЕР: ****** %s", message)
        msg_type = message.get("type")
        
        match msg_type:
            case "conversation.item.input_audio_transcription.completed":
                transcript = message.get("transcript", "")
                if transcript:
                    logger.info("👤 Пользователь: %s", transcript)
            
            case "response.output_text.delta":
                delta = message.get("delta", "")
                if delta:
                    logger.info("🤖 Ассистент: %r", delta)
            
            case "response.done":
                pass  # No special handling needed
            
            case "session.created":
                session_id = (message.get("session") or {}).get("id")
                logger.info("✅ Сессия создана: %s", session_id)
            
            case "input_audio_buffer.speech_started":
                play_epoch += 1
                current_response_epoch = None
                current_text_buffer.clear()  # Очищаем буфер при начале новой речи
                logger.debug("🎤 Пользователь начал говорить")
                await audio_out.clear()
            
            case "response.created":
                current_response_epoch = play_epoch
                current_text_buffer.clear()  # Очищаем буфер при начале нового ответа
            
            case "response.output_audio.delta":
                if current_response_epoch == play_epoch:
                    delta = message["delta"]
                    decoded = b64_decode(delta)
                    logger.debug("🔊 Получено %d байт аудио", len(decoded))
                    await audio_out.write(decoded)
            
            case "response.output_item.done":
                item = message.get("item") or {}
                if item.get("type") != 'function_call':
                    logger.debug('Получен не function_call: %r', item.get("type"))
                    continue
                
                payload_item = process_function_call(item)
                
                # Проверяем, завершается ли диалог
                function_name = item.get("name")
                if function_name == "end_dialog":
                    session_ending = True
                    logger.info("👋 Диалог завершается...")
                
                logger.info("🔧 Вызов функции: %s", item.get("name"))
                await ws.send_json(payload_item)
                
                # Если диалог завершается - не запрашиваем новый ответ
                if session_ending:
                    # Запрашиваем финальный ответ с прощальной фразой
                    await ws.send_json({
                        "type": "response.create",
                        "response": {
                            "modalities": ["audio"],
                        }
                    })
                    # Ответ возвращается в модель, параллельно ставится таймер на 2с
                    async def close_session_after_delay():
                        await asyncio.sleep(2)
                        logger.info("👋 Завершение сессии")
                        # Закрываем WebSocket соединение
                        await ws.close()
                        
                    # Запускаем таймер параллельно
                    asyncio.create_task(close_session_after_delay())
                    # Продолжаем обработку сообщений пока не сработает таймер
                    continue
                
                # Запрос нового ответа после обработки функции
                await ws.send_json({
                    "type": "response.create"
                })
            
            case "error":
                logger.error("❌ ОШИБКА СЕРВЕРА: %s", json.dumps(message, ensure_ascii=False))
            
            case other:
                logger.debug('Событие: %s', other)
    
    logger.info("🔌 WebSocket соединение закрыто")


async def uplink(ws):
    """Отправка аудио с микрофона на сервер."""
    mic = AsyncMicrophone(samplerate=IN_RATE)
    async for pcm in mic:
        logger.debug('📤 Отправка %d байт аудио', len(pcm))
        
        try:
            await ws.send_json({
                "type": "input_audio_buffer.append",
                "audio": b64_encode(pcm)
            })
        except aiohttp.ClientConnectionResetError:
            logger.warning("⚠️ WebSocket закрыт, остановка отправки аудио")
            return


async def main():
    print("=" * 70)
    print("🏋️  ГОЛОСОВОЙ ФИТНЕС-АССИСТЕНТ")
    print("=" * 70)
    print("Говорите в микрофон. Для выхода нажмите Ctrl+C.")
    print()
    print("Возможности:")
    print("  • Расчет калорий: 'Рассчитай мои калории'")
    print("  • Рекомендации добавок: 'Какие добавки мне нужны?'")
    print("  • Поиск информации: 'Найди информацию о...'")
    print()
    print("⚠️  ВАЖНО: Используйте наушники, иначе звук синтеза будет")
    print("   активировать распознавание речи!")
    print("=" * 70)
    print()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                WSS_URL,
                headers=HEADERS,
                heartbeat=20.0
            ) as ws:
                logger.info("🚀 Подключено к Realtime API")
                await setup_session(ws)
                
                async with AsyncAudioOut(samplerate=OUT_RATE) as audio_out:
                    await asyncio.gather(
                        uplink(ws),
                        downlink(ws, audio_out)
                    )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("👋 Выход из программы")


if __name__ == "__main__":
    asyncio.run(main())
