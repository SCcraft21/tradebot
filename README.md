<p style="color: blue; font-family: 'Arial', sans-serif; font-size: 20px;">

# TradeBot 🤖📈

An advanced, automated cryptocurrency trading bot driven by autonomous agentic workflows. TradeBot leverages LLM-based intelligence to analyze market trends, execute strategic trades, and manage risk with production-grade reliability.

## 🌟 Features

* **Agentic Execution:** Powered by intelligent, multi-step decision-making and real-time market analysis.
* **Real-Time Data Integration:** Continuous monitoring of market liquidity, order books, and price action.
* **Fail-Safe Risk Management:** Built-in circuit breakers, strict stop-loss/take-profit guards, and exposure caps to protect capital.
* **Robust Logging & Monitoring:** Comprehensive, structured logging for auditing trades, API states, and strategy performance.
* **Modular Architecture:** Easily plug in new technical indicators, strategies, or exchange connectors.

---

## 🏗️ Architecture & Workflow

TradeBot operates on a decoupled, agentic architecture that separates market data ingestion from the execution strategy.

```
[ Market Data ] ---> [ Agentic Decision Loop ] ---> [ Risk Validator ] ---> [ Exchange API ]
                             |
                   [ Gemini Strategy Engine ]

```

1. **Ingestion:** Live market data is fetched and formatted.
2. **Analysis:** The core engine orchestrates the decision-making process, utilizing LLM-driven insights to evaluate market sentiment and technical setups.
3. **Validation:** A localized, hard-coded Risk Module intercepts all proposed trades to ensure compliance with capital constraints.
4. **Execution:** Validated orders are dispatched safely to the exchange.

---

## 🚀 Getting Started

### Prerequisites

* Python 3.10+
* An exchange account (with API keys enabled for trading)
* Gemini API Key

### Installation

1. **Clone the repository:**
```bash
git clone https://github.com/yourusername/tradebot.git
cd tradebot

```



```

2. **Set up a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`

```

3. **Install dependencies:**

pip install -r requirements.txt

```

### Configuration

Create a `.env` file in the root directory and populate it with your environment variables:

```env
# API Keys
GEMINI_API_KEY=your_gemini_api_key_here
EXCHANGE_API_KEY=your_exchange_key
EXCHANGE_API_SECRET=your_exchange_secret

# Runtime Config
ENV=production
LOG_LEVEL=INFO

# Risk Parameters
MAX_RISK_PER_TRADE=0.02  # Maximum 2% capital exposure per trade
DAILY_LOSS_LIMIT=0.05   # Halt trading if daily loss hits 5%

```

---

## 🛠️ Usage

To run the trading bot in dry-run (simulation) mode:

```bash
python main.py --mode dry-run

```

To deploy the bot to live markets:

```bash
python main.py --mode live

```

---

## 📊 Logging & Diagnostics

Logs are systematically written to both stdout and the `logs/` directory.

* `tradebot.log`: Tracks standard operational workflows and strategy evaluations.
* `audit.log`: Exclusively tracks executed orders, fills, and risk validation checkpoints.

---

## 🔒 Security & Risk Disclaimer

**Disclaimer:** This software is for educational and research purposes only. Crypto trading involves substantial risk of loss. Never deploy keys with withdrawal permissions enabled. Ensure your `.env` file is added to your `.gitignore` to prevent exposing sensitive API credentials.
</p>

---


