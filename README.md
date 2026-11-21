# Auto-Clip-Twitch
Free Python auto-clipper for Twitch VODs: 9:16 Split-Screen, Whisper subtitles &amp; web review UI. 
🇷🇺 Сделал бесплатную прогу, которая сама нарезает стримы с Twitch в Shorts и TikTok (без водяных знаков и подписок)

    ⚠️ Дисклеймер: Весь этот проект с нуля навайбкодили в диалоге с нейросетями. Код рабочий, режет и рендерит как надо, но под капотом это чистый вайбкодинг — без претензий на идеальную архитектуру, зато быстро, бесплатно и решает задачу.

После каждого стрима сидеть по два часа, пересматривать запись, искать смешные моменты, кропать вебкамеру и вручную лепить субтитры — та ещё рутина. А платные сервисы вроде OpusClip или Eklipse просят подписку в долларах, режут качество и ставят свои вотермарки.

В итоге навайбкодил свой небольшой софт на Python, который делает всю грязную работу сам прямо на твоём компе — бесплатно и без ограничений по часам.
Что она вообще делает

    Сама находит крутые моменты. Кидаешь ей ссылку на свой прошедший стрим с Twitch (или просто готовый видос с компа). Программа пробегается по звуковой дорожке, находит места, где ты громко смеёшься, кричишь или тащишь сложный раунд, и вырезает их.

    Сразу собирает вертикальный формат 9:16. Никаких унылых чёрных полос или мыла по краям. Прога сама вырезает твою вебкамеру наверх экрана, а центр игры с прицелом ставит вниз — как в топовых нарезках по CS2 или Dota 2.

    Накладывает субтитры. Встроенная нейросеть распознаёт твою речь и лепит крупные субтитры прямо посередине экрана, подсвечивая каждое слово, когда ты его произносишь.

    Открывает удобный сайт для отбора. Как только нарезка пошла, в браузере открывается локальная страничка. Там можно сразу посмотреть готовые клипы и в один клик решить судьбу каждого: нажать «Норм» (видос улетит в папку с готовыми клипами) или «Удалить» (сразу сотрётся с диска, чтобы не занимать место).

Как запустить у себя

Всё ставится за 3 минуты, справляется обычный домашний ПК:

    Ставим Python (версию 3.11 или 3.12 с официального сайта, при установке обязательно ставим галочку Add Python to PATH).

    Ставим FFmpeg (нужен для склейки видео). Открываем PowerShell на Windows и вставляем команду:winget install --id=Gyan.FFmpeg -e --source winget(После этого окно PowerShell нужно закрыть и открыть заново).

    Запускаем саму прогу. Закидываем файл скрипта (vod_lokal_site.py) в любую папку, открываем там PowerShell и вводим по очереди:
    PowerShell

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install numpy faster-whisper yt-dlp
    python vod_lokal_site.py

Сразу после этого откроется браузер с панелью управления: вставляешь ссылку на VOD, выставляешь рамку своей вебкамеры в процентах, жмёшь «Нарезать стрим» — и через пару минут забираешь готовые шортсы.
🇺🇸 I Vibe-Coded a Free App That Automatically Cuts Twitch Streams into Shorts & TikToks (No Watermarks, No Subscriptions)

    ⚠️ Disclaimer: This entire project was 100% vibe-coded with AI. It works, cuts, and renders clips properly, so don't expect enterprise-grade software architecture — it's pure vibe-coding built fast and free to get the job done.

Sitting through a 3-hour VOD after every stream just to find funny moments, crop your webcam, and manually add subtitles is exhausting. And paid AI clippers like OpusClip or Eklipse charge monthly subscriptions in dollars, limit your minutes, and slap watermarks on your videos.

So I vibe-coded my own lightweight Python tool that does all the heavy lifting locally on your PC — completely free and with zero limits.
What It Actually Does

    Finds the best moments on its own. Just paste a link to your past Twitch broadcast (or drop in a local video file). The app scans the audio track, picks up the moments where you're laughing, yelling, or clutching a tense round, and cuts them out.

    Builds a clean 9:16 vertical layout. No ugly black bars or blurry side-fills. It automatically crops your webcam into the top third of the screen and places the centered gameplay (with your crosshair and HUD) right below it — just like top CS2 or Dota 2 clips.

    Adds word-by-word subtitles. A built-in AI model transcribes your voice and puts bold subtitles right in the middle of the screen, highlighting every word as you say it.

    Opens a local web dashboard to sort clips. As soon as rendering starts, a simple web page pops up in your browser. You can watch every generated clip right there and decide its fate in one click: hit “Approve” (moves the video straight into your ready-to-upload folder) or “Delete” (wipes it off your drive immediately).

How to Run It Yourself

Setup takes about 3 minutes and runs fine on a regular home PC:

    Install Python (version 3.11 or 3.12 from the official website — just make sure to check "Add Python to PATH" during installation).

    Install FFmpeg (used for video cutting and rendering). Open PowerShell on Windows and run:winget install --id=Gyan.FFmpeg -e --source winget(Close and reopen PowerShell afterward so Windows recognizes it).

    Launch the app. Put the script file (vod_lokal_site.py) into any folder, open PowerShell inside that folder, and run these commands one by one:
    PowerShell

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install numpy faster-whisper yt-dlp
    python vod_lokal_site.py

Your browser will open the control panel right away: paste your VOD link, set your webcam crop area in percentages, hit “Clip Stream”, and grab your ready-to-post Shorts a couple of minutes later.
