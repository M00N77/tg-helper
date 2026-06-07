"""Присутствие на встречах: захват звука с Яндекс Телемоста."""
import asyncio
import io
import os
import wave
from typing import Optional, Callable

import numpy as np

AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "virtual-audio-capturer")

try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


class MeetingListener:
    """Захват звука с Яндекс Телемоста через драйвер браузера"""
    
    def __init__(self, meeting_url: str):
        self.url = meeting_url
        self.driver: Optional[webdriver.Chrome] = None
        self.is_recording = False
        self.audio_callback: Optional[Callable] = None
        self.stream: Optional[sd.InputStream] = None
    
    async def join_meeting(
        self, display_name: str = "Бот-ассистент"
    ) -> bool:
        if not SELENIUM_AVAILABLE:
            raise RuntimeError(
                "selenium не установлен. Установи: pip install selenium"
            )
        try:
            chrome_options = Options()
            chrome_options.add_argument("--use-fake-ui-for-media-stream")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option(
                "excludeSwitches", ["enable-automation"]
            )

            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.get(self.url)
            await asyncio.sleep(4)

            # ШАГ 1 — "Продолжить в браузере"
            clicked = self.driver.execute_script("""
                const btn = document.querySelector(
                    '[class*="continueInBrowserButton"]'
                );
                if (btn) { btn.click(); return true; }
                return false;
            """)
            if not clicked:
                print("Кнопка 'Продолжить в браузере' не найдена")
            await asyncio.sleep(3)

            # ШАГ 2 — Вводим имя
            self.driver.execute_script("""
                const input = document.querySelector(
                    '[data-testid="orb-textinput-input"]'
                );
                if (input) {
                    const nativeInput = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    nativeInput.call(input, arguments[0]);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                }
            """, display_name)
            await asyncio.sleep(1)

            # ШАГ 3 — "Подключиться"
            self.driver.execute_script("""
                const btn = document.querySelector(
                    '[data-testid="enter-conference-button"]'
                );
                if (btn) btn.click();
            """)
            await asyncio.sleep(5)

            return True

        except Exception as e:
            print(f"Ошибка подключения к встрече: {e}")
            return False

    async def start_recording_via_ui(self) -> bool:
        """Запустить запись через меню Телемоста"""
        try:
            await asyncio.sleep(3)

            # ШАГ 4 — Кнопка "Ещё" (три точки)
            self.driver.execute_script("""
                const btn = document.querySelector(
                    '[data-testid="more-popup-alt-button"]'
                );
                if (btn) btn.click();
            """)
            await asyncio.sleep(2)

            # ШАГ 5 — "Записать на компьютер"
            clicked = self.driver.execute_script("""
                const option = document.querySelector(
                    '[title="Записать на компьютер"]'
                );
                if (option) { option.click(); return true; }
                return false;
            """)
            await asyncio.sleep(2)
            return bool(clicked)

        except Exception as e:
            print(f"Ошибка запуска записи через UI: {e}")
            return False

    async def stop_recording_via_ui(self) -> bool:
        """Остановить запись через UI Телемоста"""
        try:
            clicked = self.driver.execute_script("""
                const btn = document.querySelector(
                    '[class*="stopButtonContent"]'
                )?.closest('button');
                if (btn) { btn.click(); return true; }
                return false;
            """)
            await asyncio.sleep(3)
            return bool(clicked)
        except Exception as e:
            print(f"Ошибка остановки записи: {e}")
            return False

    async def start_recording(self, callback: Callable = None) -> None:
        """Начать запись звука"""
        if not SOUNDDEVICE_AVAILABLE:
            raise RuntimeError(
                "sounddevice не установлен. Установи: pip install sounddevice"
            )
        self.is_recording = True
        self.audio_callback = callback
        
        def audio_callback(indata, frames, time, status):
            if self.audio_callback and self.is_recording:
                self.audio_callback(indata.copy())
        
        # Захват с системного аудио (loopback)
        self.stream = sd.InputStream(
            samplerate=16000,
            channels=1,
            callback=audio_callback,
            device=AUDIO_DEVICE,
        )
        self.stream.start()
    
    async def capture_audio_duration(self, duration_seconds: int = 300) -> bytes:
        """Записать аудио заданной длительности"""
        audio_chunks = []
        
        def callback(chunk):
            audio_chunks.append(chunk)
        
        await self.start_recording(callback)
        await asyncio.sleep(duration_seconds)
        await self.stop_recording()
        
        # Конвертируем в WAV
        if audio_chunks:
            audio_data = np.concatenate(audio_chunks)
            
            # Нормализация
            audio_data = audio_data / np.max(np.abs(audio_data)) if np.max(np.abs(audio_data)) > 0 else audio_data
            audio_data = (audio_data * 32767).astype(np.int16)
            
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_data.tobytes())
            
            return buffer.getvalue()
        
        return b''
    
    async def stop_recording(self) -> None:
        """Остановить запись"""
        self.is_recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
    
    async def leave_meeting(self) -> None:
        """Покинуть встречу"""
        if self.driver:
            try:
                # Нажимаем "Выйти"
                leave_button = self.driver.find_element(By.XPATH, "//button[contains(text(), 'Выйти')]")
                leave_button.click()
            except Exception:
                pass
            
            self.driver.quit()
        
        await self.stop_recording()