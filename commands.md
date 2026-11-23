
**TERMUX SETUP GUIDE** · Python · SQLite

Premium OTP Bot আপনার Android Termux-এ সেটআপ ও রান করার গাইড।

## 01. Update Termux

``` bash
pkg update -y && pkg upgrade -y
```

## 02. Install Requirements

``` bash
pkg install python nano git -y
```

## 03. Create Project Folder

``` bash
mkdir -p ~/premium-otp-bot
cd ~/premium-otp-bot
```

## 04. Create Bot File

``` bash
nano bot.py
```

আপনার সম্পূর্ণ Python Bot Code Paste করুন।

**Save করার নিয়ম:** - `CTRL + X` - `Y` - `ENTER`

## 05. Install Python Libraries

প্রথমে pip আপডেট করুন:

``` bash
python -m pip install --upgrade pip
```

প্রয়োজনীয় লাইব্রেরি ইনস্টল করুন:

``` bash
pip install -U python-telegram-bot httpx
```

## 06. Configure Bot

``` bash
nano bot.py
```

নিজের Bot Token এবং Telegram ID সেট করুন:

``` python
BOT_TOKEN = "YOUR_BOT_TOKEN"
OWNER_IDS = [YOUR_TELEGRAM_ID]
```

ফাইলটি Save করুন। নিজের Bot Token কারও সঙ্গে শেয়ার করবেন না।

## 07. Start Premium OTP Bot

``` bash
cd ~/premium-otp-bot
python bot.py
```

Bot সফলভাবে চালু হলে Terminal-এ Startup Message দেখতে পাবেন।

## 08. Run Bot in the Background (Optional)

TMUX ইনস্টল করুন:

``` bash
pkg install tmux -y
```

নতুন TMUX Session তৈরি করুন:

``` bash
tmux new -s premium
```

Bot চালু করুন:

``` bash
cd ~/premium-otp-bot
python bot.py
```

Background-এ রাখতে `CTRL + B`, তারপর `D` চাপুন।

আবার Session-এ ফিরতে:

``` bash
tmux attach -t premium
```

## 09. Stop Bot

``` bash
pkill -f "python bot.py"
```

## 10. Database Location

``` text
~/premium-otp-bot/premium_bot.db
```

এখানে Bot-এর SQLite Database সংরক্ষিত থাকবে। Database ফাইলটি Delete করবেন
না, এতে সংরক্ষিত তথ্য হারাতে পারেন।
