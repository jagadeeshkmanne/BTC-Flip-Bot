window.CURRICULUM_DATA = {
  "modules": [
    {
      "num": 1,
      "name": "Python For AI Engineers",
      "time": "Time: 10-12h. Cumulative: 12h.",
      "videos": [
        {
          "order": 1,
          "title": "Python for AI - Full Beginner Course",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=ygXn5nV5qFc",
          "id": "ygXn5nV5qFc",
          "duration": "5h 15m",
          "difficulty": "Easy",
          "why": "Best AI-focused Python path because it teaches the Python you need for LLM apps, not academic data science.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Python Pydantic Tutorial: Complete Data Validation Course",
          "creator": "Corey Schafer",
          "url": "https://www.youtube.com/watch?v=M81pfi64eeM",
          "id": "M81pfi64eeM",
          "duration": "1h 29m",
          "difficulty": "Medium",
          "why": "High-quality hands-on Pydantic validation course from one of the strongest Python educators.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "Pydantic v2 Full Course - Python Data Validation",
          "creator": "ArjanCodes",
          "url": "https://www.youtube.com/watch?v=Vj-iU-8_xLs",
          "id": "Vj-iU-8_xLs",
          "duration": "45m",
          "difficulty": "Medium",
          "why": "Explains production-grade typed models, validation, JSON parsing, and clean architecture patterns.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Asyncio in Python - Complete Tutorial for Backend Developers",
          "creator": "mCoding",
          "url": "https://www.youtube.com/watch?v=K56nNuBEd0c",
          "id": "K56nNuBEd0c",
          "duration": "35m",
          "difficulty": "Advanced",
          "why": "Best practical mental model for async API calls, concurrency, and non-blocking LLM gateways.",
          "skip": "Skip the low-level history if you already understand async/await."
        }
      ],
      "desktop": {
        "id": 1,
        "part": 1,
        "partName": "PART 1: Foundation, Embeddings & RAG",
        "title": "Python for Enterprise Engineers",
        "time": "5 Hours",
        "cumTime": "5h (2.3%)",
        "techs": [
          "Python for AI Engineers",
          "Asyncio",
          "Pydantic v2",
          "uv / poetry"
        ],
        "goal": "Write idiomatic, type-safe, production-grade Python code that rivals your Spring Boot Java standards. Master virtual environments, type hinting, Pydantic data validation, and asynchronous event loops without data science fluff.",
        "whyMatters": "While Java dominates backend transaction processing, the entire global AI ecosystem is built natively in Python. Companies need engineers who can build robust Python microservices that integrate seamlessly with existing Spring Boot services.",
        "javaAnalogy": "Pydantic v2 is your Lombok + Jackson + Bean Validation (@Valid) combined. Asyncio is your WebFlux / Netty non-blocking I/O event loop.",
        "videos": [
          {
            "step": "1.1",
            "title": "Python for AI Developers: Modern Best Practices (2025/2026)",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=0kF_-K9S61c",
            "duration": "1h 15m",
            "difficulty": "Intermediate",
            "whyBest": "Dave skips beginner loops and variables entirely. He focuses on setting up modern Python environments (uv/poetry), type annotations, Pydantic v2 data models, and enterprise project structuring.",
            "skip": "Skip introductory remarks about why Python is popular; jump straight to project setup."
          }
        ],
        "miniProject": "Pydantic REST Client Wrapper: Build a strongly-typed async Python client that fetches data from a public REST API using httpx and validates payloads into strict Pydantic v2 models.",
        "prodProject": "Enterprise Microservice Config & Logging Framework: Build a reusable foundation library with structlog JSON logging, pydantic-settings, and HTTP 429 rate limit retry loops.",
        "repos": [
          {
            "name": "pydantic/pydantic",
            "desc": "Industry standard data validation. Study BaseClass and JSON serialization.",
            "url": "https://github.com/pydantic/pydantic"
          },
          {
            "name": "encode/httpx",
            "desc": "Next-gen async HTTP client for Python connection pooling.",
            "url": "https://github.com/encode/httpx"
          }
        ],
        "docs": [
          {
            "name": "Pydantic v2 Official Docs",
            "url": "https://docs.pydantic.dev/latest/"
          },
          {
            "name": "Python Asyncio Docs",
            "url": "https://docs.python.org/3/library/asyncio.html"
          }
        ],
        "mistakes": [
          "Treating Python like dynamically-typed scripting without strict type annotations (mypy/pyright).",
          "Blocking the async event loop by using synchronous time.sleep() or requests inside async def functions."
        ],
        "interviewQA": [
          {
            "q": "How does Pydantic v2 achieve high-speed JSON parsing compared to Pydantic v1?",
            "a": "Pydantic v2 rewrote its core validation and serialization engine in Rust (pydantic-core), bypassing Python C-API overhead during parsing and achieving up to 20x performance improvements."
          },
          {
            "q": "Why is asyncio preferred over multi-threading for calling LLM APIs in Python?",
            "a": "Because Python uses a Global Interpreter Lock (GIL), multi-threading incurs memory context switching overhead without CPU parallelism. Since LLM generation is network I/O bound, asyncio cooperative multitasking scales to thousands of concurrent API calls on a single thread."
          }
        ],
        "checklist": [
          "Configured a modern Python workspace using uv with strict linting (ruff) and type checking (mypy).",
          "Created Pydantic v2 models with nested validators and JSON schema export.",
          "Written an async script using asyncio and httpx that executes 20 concurrent GET requests without blocking."
        ]
      },
      "projectVideos": [
        {
          "title": "Pydantic Tutorial • Solving Python's Biggest Problem",
          "creator": "pixegami",
          "url": "https://www.youtube.com/watch?v=XIdQ6gO3Anc",
          "duration": "11m",
          "why": "Practical Pydantic project patterns for turning messy Python dictionaries into reliable typed application models.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Asyncio in Python - Full Tutorial",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=Qb9s3UiMSTA",
          "duration": "25m",
          "why": "Useful hands-on async practice before building LLM clients that call multiple provider APIs concurrently.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 2,
      "name": "LLM API Fundamentals: OpenAI, Anthropic, Gemini",
      "time": "Time: 10-12h. Cumulative: 24h.",
      "videos": [
        {
          "order": 1,
          "title": "OpenAI Just Changed Everything (Responses API Walkthrough)",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=0pGxoubWI6s",
          "id": "0pGxoubWI6s",
          "duration": "29m",
          "difficulty": "Medium",
          "why": "Best current OpenAI application API walkthrough for production app builders.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "The OpenAI (Python) API - Introduction & Example Code",
          "creator": "Shaw Talebi",
          "url": "https://www.youtube.com/watch?v=czvVibB2lRA",
          "id": "czvVibB2lRA",
          "duration": "23m",
          "difficulty": "Easy",
          "why": "Clean Python SDK walkthrough without unnecessary theory.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "Getting Started with Tool Use in the Anthropic API",
          "creator": "Ram Vegiraju",
          "url": "https://www.youtube.com/watch?v=7xVmf9lIj14",
          "id": "7xVmf9lIj14",
          "duration": "14m 4s",
          "difficulty": "Medium",
          "why": "Newer Anthropic API-focused walkthrough for current Claude tool-use patterns, better than older model-specific Claude guides.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "The Gemini Interactions API",
          "creator": "Sam Witteveen",
          "url": "https://www.youtube.com/watch?v=aZgH_wnmedQ",
          "id": "aZgH_wnmedQ",
          "duration": "24m",
          "difficulty": "Medium",
          "why": "Better Gemini-native mental model than generic starter clips.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "Build agents with Gemini API (I/O Connect 2026)",
          "creator": "Google for Developers",
          "url": "https://www.youtube.com/watch?v=d9LAQWKUnx8",
          "id": "d9LAQWKUnx8",
          "duration": "37m 10s",
          "difficulty": "Medium",
          "why": "Current Google Gemini API agent walkthrough that is more relevant than older current Gemini long-context demos.",
          "skip": "Skip event intro if you only want implementation."
        }
      ],
      "desktop": {
        "id": 2,
        "part": 1,
        "partName": "PART 1: Foundation, Embeddings & RAG",
        "title": "LLM API Fundamentals: OpenAI, Anthropic, Gemini",
        "time": "10 Hours",
        "cumTime": "15h (6.8%)",
        "techs": [
          "OpenAI API",
          "Anthropic API",
          "Gemini API",
          "REST Clients"
        ],
        "goal": "Programmatically integrate the world's top frontier language models into enterprise backend applications using official Python SDKs. Master token accounting, temperature sampling, error handling, and SSE streaming.",
        "whyMatters": "Enterprise AI engineering involves consuming LLMs as cloud-based Intelligence-as-a-Service (IaaS) primitives. Knowing how to architect failover-tolerant wrappers around these REST APIs is the #1 foundational skill.",
        "javaAnalogy": "Official Python SDKs are strongly-typed REST Templates / Feign Clients that handle connection pooling, exponential backoff, and Server-Sent Events (SSE) streaming.",
        "videos": [
          {
            "step": "2.1",
            "title": "OpenAI API Python SDK Complete Guide",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=c-g6epk3fFE",
            "duration": "45m",
            "difficulty": "Beginner",
            "whyBest": "Clean, production-oriented walkthrough of the official openai Python SDK (v1.0+ architecture), covering async clients and token usage inspection.",
            "skip": "Skip setup instructions if your .env file is ready."
          }
        ],
        "miniProject": "Multi-Provider LLM Router: Build an async Python service that accepts a user prompt and routes across OpenAI, Anthropic, and Gemini with automatic HTTP 429 failover.",
        "prodProject": "Enterprise Resilient LLM Gateway Service: Build a gateway library with token cost accounting, automated prompt caching headers, and unified streaming token generators.",
        "repos": [
          {
            "name": "openai/openai-python",
            "desc": "Official OpenAI Python SDK repository.",
            "url": "https://github.com/openai/openai-python"
          },
          {
            "name": "BerriAI/litellm",
            "desc": "Industry standard library for calling 100+ LLM APIs using unified OpenAI format.",
            "url": "https://github.com/BerriAI/litellm"
          }
        ],
        "docs": [
          {
            "name": "OpenAI API Reference",
            "url": "https://platform.openai.com/docs/api-reference"
          },
          {
            "name": "Anthropic Prompt Caching Docs",
            "url": "https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching"
          }
        ],
        "mistakes": [
          "Hardcoding API keys in source code instead of environment variables or secrets vaults.",
          "Using legacy OpenAI v0.28 static methods (openai.ChatCompletion.create) which fail in modern runtimes."
        ],
        "interviewQA": [
          {
            "q": "Why is Server-Sent Events (SSE) mandatory for AI user interfaces instead of HTTP polling?",
            "a": "Because LLM generation takes 10+ seconds. Synchronous HTTP blocks and times out on load balancers (e.g. AWS ALB 60s timeout). SSE streams token-by-token chunks as emitted by the GPU, reducing Time-To-First-Token (TTFT) under 500ms."
          },
          {
            "q": "What is Anthropic Prompt Caching and how does it save enterprise costs?",
            "a": "Prompt caching stores computed Key-Value (KV) attention activations of static system prompts on inference edge servers. Repeated requests skip embedding and attention computation, reducing latency by 85% and input costs by 90%."
          }
        ],
        "checklist": [
          "Initialized sync and async clients for OpenAI, Anthropic, and Gemini using environment variables.",
          "Implemented an asynchronous streaming endpoint that yields tokens in real-time.",
          "Built Circuit Breaker fallback wrappers that catch rate limit exceptions and switch providers."
        ]
      },
      "projectVideos": [
        {
          "title": "OpenAI Api Crash Course For Beginners | Financial Data Extraction Tool Using OpenAI API",
          "creator": "codebasics",
          "url": "https://www.youtube.com/watch?v=xP_ZON_P4Ks",
          "duration": "25m",
          "why": "Good enterprise-style extraction use case for learning provider APIs with a real business document workflow.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Gemini API with Python - Getting Started Tutorial",
          "creator": "Patrick Loeber",
          "url": "https://www.youtube.com/watch?v=qfWpPEgea2A",
          "duration": "12m",
          "why": "Clean Gemini API project starter from a strong Python educator.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Guide to Agentic AI - Build a Python Coding Agent with Gemini",
          "creator": "freeCodeCamp.org",
          "url": "https://www.youtube.com/watch?v=YtHdaXuOAks",
          "duration": "2h 14m",
          "why": "Deeper Gemini project path that turns provider API basics into a useful agentic coding assistant.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 3,
      "name": "Prompt Engineering, Structured Outputs, Tool Calling",
      "time": "Time: 10-12h. Cumulative: 36h.",
      "videos": [
        {
          "order": 1,
          "title": "Prompt Engineering Tutorial - Master ChatGPT and LLM Responses",
          "creator": "freeCodeCamp.org",
          "url": "https://www.youtube.com/watch?v=_ZvnD73m40o",
          "id": "_ZvnD73m40o",
          "duration": "42m",
          "difficulty": "Easy",
          "why": "Practical, broad, not math-heavy.",
          "skip": "Skip consumer productivity examples."
        },
        {
          "order": 2,
          "title": "OpenAI Structured Output - All You Need to Know",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=fuMKrKlaku4",
          "id": "fuMKrKlaku4",
          "duration": "25m",
          "difficulty": "Medium",
          "why": "Strong bridge from prompts to typed outputs.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "OpenAI Responses API: Structured Outputs with Pydantic",
          "creator": "Leon van Zyl",
          "url": "https://www.youtube.com/watch?v=3Z03fwH1I7s",
          "id": "3Z03fwH1I7s",
          "duration": "5m",
          "difficulty": "Easy",
          "why": "Quick Pydantic pattern reinforcement.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "OpenAI Function Calling - Full Beginner Tutorial",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=aqdWSYWC_LI",
          "id": "aqdWSYWC_LI",
          "duration": "28m",
          "difficulty": "Medium",
          "why": "Practical tool/function calling.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "What is Tool Calling? Connecting LLMs to Your Data",
          "creator": "IBM Technology",
          "url": "https://www.youtube.com/watch?v=h8gMhXYAv1k",
          "id": "h8gMhXYAv1k",
          "duration": "5m",
          "difficulty": "Easy",
          "why": "Crisp conceptual explanation for interviews.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 3,
        "part": 1,
        "partName": "PART 1: Foundation, Embeddings & RAG",
        "title": "Prompt Engineering, Structured Outputs & Tool Calling",
        "time": "12 Hours",
        "cumTime": "27h (12.3%)",
        "techs": [
          "Prompt Engineering",
          "Structured Outputs",
          "Tool Calling",
          "Instructor"
        ],
        "goal": "Transition from conversational prompting to deterministic engineering. Enforce guaranteed JSON structured outputs using Pydantic and implement deterministic Tool Calling (Function Calling) that allows LLMs to query databases and invoke APIs.",
        "whyMatters": "Probabilistic markdown text is useless for backend integrations. A Spring Boot order microservice requires validated JSON payloads. Tool Calling is what transforms LLMs from chatbots into autonomous software agents.",
        "javaAnalogy": "OpenAI Strict Schema is like GraphQL schema compilation combined with Hibernate Bean Validation—it physically prevents invalid syntax generation at the GPU inference level.",
        "videos": [
          {
            "step": "3.1",
            "title": "Structured Outputs in OpenAI — Guaranteed JSON Schema",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=0d80Z0l5h1k",
            "duration": "40m",
            "difficulty": "Intermediate",
            "whyBest": "Demonstrates how response_format={'type': 'json_schema'} constrains model token sampling. Compares raw API usage with the Instructor library.",
            "skip": "Skip basic JSON formatting overview; focus on strict: true mechanics."
          },
          {
            "step": "3.2",
            "title": "LLM Function Calling / Tool Calling Masterclass",
            "creator": "Sam Witteveen",
            "url": "https://www.youtube.com/watch?v=W01f13b-pI8",
            "duration": "45m",
            "difficulty": "Intermediate",
            "whyBest": "Deep architectural walkthrough of how tool definitions are injected into context windows and how your backend executes tools and returns observations.",
            "skip": "None."
          },
          {
            "step": "3.3",
            "title": "Advanced Prompt Engineering for Developers (OpenAI / Andrew Ng)",
            "creator": "DeepLearning.AI",
            "url": "https://www.youtube.com/watch?v=dL0wPz2t3pI",
            "duration": "50m",
            "difficulty": "Intermediate",
            "whyBest": "Focuses on developer techniques: delimiters, conditional execution branching inside prompts, few-shot formatting, and Chain-of-Thought (CoT).",
            "skip": "None."
          }
        ],
        "miniProject": "Unstructured Invoice to Pydantic Extractor: Build a service that takes messy OCR invoice text and extracts it into a strict Pydantic model with decimal and date coercion.",
        "prodProject": "Enterprise Autonomous SQL Assistant: Build an agent decorated with SQL read tools, AST SQL syntax sanitization (blocking DROP/DELETE), and parallel tool execution loops.",
        "repos": [
          {
            "name": "jxnl/instructor",
            "desc": "Gold standard library for structured Pydantic data extraction across any LLM.",
            "url": "https://github.com/jxnl/instructor"
          },
          {
            "name": "pydantic/pydantic-ai",
            "desc": "Pydantic official type-safe agent framework with DI support.",
            "url": "https://github.com/pydantic/pydantic-ai"
          }
        ],
        "docs": [
          {
            "name": "OpenAI Structured Outputs Guide",
            "url": "https://platform.openai.com/docs/guides/structured-outputs"
          },
          {
            "name": "Anthropic Tool Use (Function Calling) Docs",
            "url": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use"
          }
        ],
        "mistakes": [
          "Using regex or string stripping (replace('```json', '')) to parse LLM JSON instead of native structured schemas.",
          "Overloading tool descriptions with 30+ tools at once, causing severe tool hallucination."
        ],
        "interviewQA": [
          {
            "q": "How does OpenAI Strict Schema (strict: true) achieve 100% JSON compliance at the hardware level?",
            "a": "It converts your JSON schema into a Finite State Automaton (FSA) or Context-Free Grammar. During GPU inference token sampling, logits for grammatically invalid characters are masked to zero probability, making invalid syntax physically impossible."
          },
          {
            "q": "How do you handle partial failures in parallel tool calling loops?",
            "a": "Execute tools via asyncio.gather(..., return_exceptions=True). If one tool fails (e.g. API 500), return the error message formatted inside the tool observation block ({'error': 'API timeout'}). The LLM reads the error and autonomously decides to retry or inform the user without crashing the loop."
          }
        ],
        "checklist": [
          "Enforced strict JSON schema extraction using Pydantic and OpenAI parse() SDK methods.",
          "Built an automated Chain-of-Thought (CoT) prompt template for complex logic tasks.",
          "Implemented an async tool execution loop that handles parallel tool calls cleanly."
        ]
      },
      "projectVideos": [
        {
          "title": "Build Production-Ready AI Agents in Python with Pydantic AI",
          "creator": "ArjanCodes",
          "url": "https://www.youtube.com/watch?v=-WB0T0XmDrY",
          "duration": "16m",
          "why": "Good code-first project for typed agent outputs and schema-first engineering.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Instructor and Pydantic - Structured LLM outputs for easy data extraction!",
          "creator": "BugBytes",
          "url": "https://www.youtube.com/watch?v=3xUW1Do9zOs",
          "duration": "17m",
          "why": "Useful project for schema-first extraction with Instructor and Pydantic.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 4,
      "name": "AI Backend APIs With FastAPI",
      "time": "Time: 10-12h. Cumulative: 46h.",
      "videos": [
        {
          "order": 1,
          "title": "FastAPI for AI Projects - Getting Started in 15 Minutes",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=-IaCV5-mlSk",
          "id": "-IaCV5-mlSk",
          "duration": "16m",
          "difficulty": "Easy",
          "why": "AI-specific FastAPI path.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Microservices with FastAPI - Full Course",
          "creator": "freeCodeCamp.org",
          "url": "https://www.youtube.com/watch?v=Cy9fAvsXGZA",
          "id": "Cy9fAvsXGZA",
          "duration": "1h 29m",
          "difficulty": "Medium",
          "why": "Production API structure.",
          "skip": "Skip basics you know."
        },
        {
          "order": 3,
          "title": "Event-Driven Architecture with React and FastAPI",
          "creator": "freeCodeCamp.org",
          "url": "https://www.youtube.com/watch?v=NVvIpqmf_Xc",
          "id": "NVvIpqmf_Xc",
          "duration": "1h 38m",
          "difficulty": "Medium",
          "why": "Useful for async workflows.",
          "skip": "UI portions optional."
        },
        {
          "order": 4,
          "title": "FastAPI Beyond CRUD Full Course",
          "creator": "Ssali Jonathan",
          "url": "https://www.youtube.com/watch?v=TO4aQ3ghFOc",
          "id": "TO4aQ3ghFOc",
          "duration": "12h 53m",
          "difficulty": "Advanced",
          "why": "Deep reference for auth/workers/testing.",
          "skip": "Use as reference, not full watch."
        },
        {
          "order": 5,
          "title": "How To Build an API with Python (LLM Integration...)",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=cy6EAp4iNN4",
          "id": "cy6EAp4iNN4",
          "duration": "21m",
          "difficulty": "Easy",
          "why": "AI endpoint implementation.",
          "skip": "Local-only parts optional."
        }
      ],
      "desktop": {
        "id": 13,
        "part": 3,
        "partName": "PART 3: Backend Infrastructure & Data Tier",
        "title": "AI Backend APIs With FastAPI",
        "time": "15 Hours",
        "cumTime": "145h (65.9%)",
        "techs": [
          "FastAPI",
          "Docker",
          "Kubernetes",
          "Async Web Servers",
          "GPU Scheduling"
        ],
        "goal": "Architect asynchronous, high-throughput Python REST APIs using FastAPI. Containerize AI microservices using multi-stage Docker builds, and understand Kubernetes deployment patterns for scheduling CPU/GPU AI workloads.",
        "whyMatters": "AI models cannot live in isolation; they must be served via robust HTTP/gRPC APIs that withstand concurrent traffic spikes, memory leaks, and connection pooling without crashing production servers.",
        "javaAnalogy": "FastAPI is your Spring Boot MVC / WebFlux framework. Pydantic dependency injection in FastAPI mirrors `@Autowired` / Spring IOC containers.",
        "videos": [],
        "miniProject": "FastAPI Streaming Service: Build a FastAPI service with an endpoint `/api/v1/chat/stream` that streams OpenAI token responses via `StreamingResponse(..., media_type='text/event-stream')`.",
        "prodProject": "Production Dockerized & K8s-Ready AI Gateway Microservice: Build a production FastAPI service with custom middleware for JWT authentication and request correlation IDs. Write a multi-stage Dockerfile and complete K8s manifests (`Deployment.yaml`, `Service.yaml`, `HPA.yaml`) configured with liveness probes and resource limits.",
        "repos": [
          {
            "name": "tiangolo/fastapi",
            "desc": "Modern, fast web framework for building APIs with Python 3.8+.",
            "url": "https://github.com/tiangolo/fastapi"
          },
          {
            "name": "astral-sh/uv-docker-example",
            "desc": "Official best practices for containerizing fast Python uv apps in Docker.",
            "url": "https://github.com/astral-sh/uv-docker-example"
          }
        ],
        "docs": [
          {
            "name": "FastAPI Dependency Injection Guide",
            "url": "https://fastapi.tiangolo.com/tutorial/dependencies/"
          },
          {
            "name": "Kubernetes Managing GPUs Docs",
            "url": "https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/"
          }
        ],
        "mistakes": [
          "Running single-process Uvicorn in production instead of using Gunicorn process managers with Uvicorn async workers (gunicorn -k uvicorn.workers.UvicornWorker).",
          "Failing to set memory requests and limits in Kubernetes, allowing memory-hungry embedding libraries to trigger Out-Of-Memory (OOMKilled) pod crashes."
        ],
        "interviewQA": [
          {
            "q": "How does FastAPI Dependency Injection (Depends) work under the hood compared to Spring Boot @Autowired?",
            "a": "Spring Boot uses reflection and a global ApplicationContext container initialized at startup to inject singletons. FastAPI evaluates functions passed to Depends() dynamically during request lifecycle routing, resolving sub-dependencies hierarchically and caching results per request scope."
          },
          {
            "q": "Why must you configure distinct Readiness and Liveness probes for AI pods in Kubernetes?",
            "a": "AI containers often take 10-30 seconds at startup to download models or initialize database pools. If Liveness probes fire too early, K8s will kill and restart the pod in an infinite loop. Readiness probes must check that models and DB pools are fully ready before routing ingress traffic, while Liveness checks basic event loop responsiveness."
          }
        ],
        "checklist": [
          "Built modular FastAPI applications using APIRouter and clean Dependency Injection.",
          "Optimized Docker container images under 400MB using multi-stage builds and virtual environments.",
          "Authored Kubernetes deployment manifests with CPU/Memory limits and readiness health checks."
        ]
      },
      "projectVideos": [
        {
          "title": "Learn Fast API With This ONE Project",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=SR5NYCdzKkc",
          "duration": "2h 6m",
          "why": "Strong project-first FastAPI course for backend engineers moving into Python services.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "cost": "Free/local or low-cost API; cloud optional where noted",
          "title": "Beginner's Guide to FastAPI & OpenAI ChatGPT API Integration | Code",
          "creator": "Pradip Nichite",
          "url": "https://www.youtube.com/watch?v=KVdP4SpWcc4",
          "duration": "31m",
          "why": "Focused FastAPI plus OpenAI integration project before the later streaming-specific module."
        },
        {
          "title": "3-Langchain Series-Production Grade Deployment LLM As API With Langchain And FastAPI",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=XWB5DXP-DO8",
          "duration": "27m",
          "why": "Production API pattern for exposing LLM apps as backend services.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "The Ultimate FastAPI + React Full Stack Project (Deploy This and You're Set)",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=_1P0Uqk50Ps",
          "duration": "3h 30m",
          "why": "Full-stack reference project for turning FastAPI into a product backend.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 5,
      "name": "Auth, Streaming And Product API Contracts",
      "time": "Time: 10-12h. Cumulative: 56h.",
      "videos": [
        {
          "order": 1,
          "title": "Secure FastAPI API with JWT (OAuth2)",
          "creator": "Code with Josh",
          "url": "https://www.youtube.com/watch?v=KxR3OONvDvo",
          "id": "KxR3OONvDvo",
          "duration": "47m",
          "difficulty": "Medium",
          "why": "Practical FastAPI auth.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "FastAPI JWT Tutorial",
          "creator": "Eric Roby",
          "url": "https://www.youtube.com/watch?v=0A_GCXBCNUQ",
          "id": "0A_GCXBCNUQ",
          "duration": "20m",
          "difficulty": "Medium",
          "why": "Shorter auth implementation.",
          "skip": "Watch as reinforcement."
        },
        {
          "order": 3,
          "title": "API Authentication: JWT, OAuth2, and More",
          "creator": "ByteMonk",
          "url": "https://www.youtube.com/watch?v=xJA8tP74KD0",
          "id": "xJA8tP74KD0",
          "duration": "6m",
          "difficulty": "Easy",
          "why": "Conceptual auth comparison.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "DataStreaming with LangChain & FastAPI",
          "creator": "Coding Crash Courses",
          "url": "https://www.youtube.com/watch?v=Gn54EbU9mRg",
          "id": "Gn54EbU9mRg",
          "duration": "9m",
          "difficulty": "Medium",
          "why": "Compact direct implementation for streaming LLM responses through a FastAPI backend.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "Real-Time Agent Applications with WebSockets & FastAPI",
          "creator": "The Neural Maze",
          "url": "https://www.youtube.com/watch?v=svABzOASrzg",
          "id": "svABzOASrzg",
          "duration": "18m",
          "difficulty": "Medium",
          "why": "Agent streaming pattern.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 16,
        "part": 3,
        "partName": "PART 3: Backend Infrastructure & Data Tier",
        "title": "Auth, Streaming And Product API Contracts",
        "time": "8 Hours",
        "cumTime": "173h (78.6%)",
        "techs": [
          "Authentication (JWT/OAuth2)",
          "SSE Streaming",
          "Celery / ARQ",
          "Background Workers"
        ],
        "goal": "Secure AI API endpoints using JWT/OAuth2 authentication. Implement Server-Sent Events (SSE) streaming for real-time frontend UI updates, and offload heavy document ingestion and embedding pipelines to asynchronous background workers (Celery/ARQ/Redis).",
        "whyMatters": "Generating 1,000 tokens takes 10+ seconds. If an HTTP request blocks for 10 seconds, load balancers (AWS ALB) time out and drop connections. Streaming SSE reduces perceived latency to 300ms, while background workers ensure heavy vector indexing never degrades API performance.",
        "javaAnalogy": "SSE streaming is Java Spring WebFlux / Reactor Flux. Celery/ARQ background workers are RabbitMQ / Kafka message consumers or `@Async` Spring task executors.",
        "videos": [
          {
            "step": "16.1",
            "title": "Streaming in React the Simple Way: Server-Sent Events (with FastAPI)",
            "creator": "Techno Pain",
            "url": "https://www.youtube.com/watch?v=hOAAg1WaZh8",
            "duration": "8m 56s",
            "difficulty": "Intermediate",
            "whyBest": "Short practical SSE streaming demo with FastAPI; enough to adapt for LLM token streaming.",
            "skip": "Frontend details can be skimmed.",
            "id": "hOAAg1WaZh8"
          }
        ],
        "miniProject": "JWT-Secured Streaming Chat Endpoint: Build a FastAPI service where `/api/chat` requires a valid JWT Bearer token in the header and streams back token responses using SSE.",
        "prodProject": "Async Document Ingestion Engine with ARQ & Redis: Build a document processing architecture where an API endpoint accepts PDF file uploads, saves them to S3/MinIO, and pushes a job ID to an ARQ Redis worker queue. The background worker parses the PDF, chunks text, generates embeddings, inserts into pgvector, and updates job status in Postgres.",
        "repos": [
          {
            "name": "samuelcolvin/arq",
            "desc": "Fast async job queues for Python built on Redis and asyncio.",
            "url": "https://github.com/samuelcolvin/arq"
          },
          {
            "name": "sysid/sse-starlette",
            "desc": "Server-Sent Events (SSE) support for Starlette and FastAPI.",
            "url": "https://github.com/sysid/sse-starlette"
          }
        ],
        "docs": [
          {
            "name": "FastAPI OAuth2 with JWT Tokens Guide",
            "url": "https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/"
          },
          {
            "name": "ARQ Async Job Queue Documentation",
            "url": "https://arq-docs.helpmanual.io/"
          }
        ],
        "mistakes": [
          "Attempting to run heavy PDF chunking and embedding loops synchronously inside a FastAPI route handler, freezing the API.",
          "Forgetting to include CORS middleware allowing frontend applications to read Server-Sent Events streams."
        ],
        "interviewQA": [
          {
            "q": "Why is ARQ preferred over traditional Celery for modern asynchronous AI document processing?",
            "a": "Celery was designed for synchronous Python workers and requires complex eventlet/gevent monkey-patching for async I/O. ARQ is built natively on Python asyncio and Redis, allowing a single background worker to process dozens of concurrent HTTP embedding API requests without blocking."
          },
          {
            "q": "How do you securely pass authentication tokens over Server-Sent Events (SSE) from a browser frontend?",
            "a": "Standard browser EventSource API does not support custom HTTP Authorization headers. In enterprise architecture, you either pass a short-lived one-time ticket JWT in the URL query parameters verified by backend middleware, or use fetch-based SSE libraries (like @microsoft/fetch-event-source) that support custom bearer headers."
          }
        ],
        "checklist": [
          "Secured FastAPI endpoints using OAuth2 with JWT token validation and role claims.",
          "Implemented SSE streaming generators that deliver tokens and status metadata simultaneously.",
          "Deployed ARQ/Redis background worker processes that process heavy embedding pipelines asynchronously."
        ]
      },
      "projectVideos": [
        {
          "title": "Build a Streaming LLM API with FastAPI in Python (Real Time Responses)",
          "creator": "Onur Baltaci",
          "url": "https://www.youtube.com/watch?v=twXxb00w1_4",
          "duration": "11m",
          "why": "Direct FastAPI SSE-style streaming endpoint project.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "FastAPI + React B2B SaaS Full Project Build - Orgs, Users, Billing, Roles & More...",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=BvsBJynm64k",
          "duration": "2h 53m",
          "why": "Enterprise-style auth, users, orgs, roles, and SaaS backend project.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 6,
      "name": "Enterprise Data Layer: PostgreSQL And Redis",
      "time": "Time: 10-12h. Cumulative: 66h.",
      "videos": [
        {
          "order": 1,
          "title": "PostgreSQL in 100 Seconds",
          "creator": "Fireship",
          "url": "https://www.youtube.com/watch?v=n2Fluyr3lbc",
          "id": "n2Fluyr3lbc",
          "duration": "3m",
          "difficulty": "Easy",
          "why": "Quick refresh.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Redis in 100 Seconds",
          "creator": "Fireship",
          "url": "https://www.youtube.com/watch?v=G1rOthIU-uo",
          "id": "G1rOthIU-uo",
          "duration": "2m",
          "difficulty": "Easy",
          "why": "Quick cache mental model.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "Redis Tutorial In 16 Minutes",
          "creator": "Eric Roby",
          "url": "https://www.youtube.com/watch?v=6nY-kci1rlo",
          "id": "6nY-kci1rlo",
          "duration": "16m",
          "difficulty": "Easy",
          "why": "FastAPI+Redis implementation.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Professional Task Queues in Python with Celery, RabbitMQ & Redis",
          "creator": "NeuralNine",
          "url": "https://www.youtube.com/watch?v=0gtdUkEzzn4",
          "id": "0gtdUkEzzn4",
          "duration": "27m",
          "difficulty": "Medium",
          "why": "Useful queue architecture intro.",
          "skip": "RabbitMQ optional."
        }
      ],
      "desktop": {
        "id": 14,
        "part": 3,
        "partName": "PART 3: Backend Infrastructure & Data Tier",
        "title": "Enterprise Data Layer: PostgreSQL And Redis",
        "time": "10 Hours",
        "cumTime": "155h (70.5%)",
        "techs": [
          "PostgreSQL",
          "pgvector",
          "Redis",
          "Semantic Caching",
          "Relational + Vector"
        ],
        "goal": "Master PostgreSQL with the `pgvector` extension as a unified enterprise database handling both ACID relational transactions and HNSW vector search. Deploy Redis for high-speed semantic caching and conversational memory.",
        "whyMatters": "Adding a standalone vector database (like Pinecone) introduces another operational system to maintain. For 80% of enterprise applications, enabling `pgvector` on existing PostgreSQL clusters delivers incredible vector search speed while keeping relational data and vector embeddings ACID-compliant in one database.",
        "javaAnalogy": "pgvector allows you to execute SQL queries that join your standard `users` table with vector cosine similarity calculations in a single JDBC/SQL transaction.",
        "videos": [
          {
            "step": "14.1",
            "title": "PGVector: Turn PostgreSQL Into A Vector Database",
            "creator": "NeuralNine",
            "url": "https://www.youtube.com/watch?v=j1QcPSLj7u0",
            "duration": "20m 4s",
            "difficulty": "Intermediate",
            "whyBest": "Hands-on pgvector implementation that fits enterprise teams already using Postgres.",
            "skip": "None.",
            "id": "j1QcPSLj7u0"
          },
          {
            "step": "14.2",
            "title": "A Semantic Cache using LangChain",
            "creator": "Redis",
            "url": "https://www.youtube.com/watch?v=LRswXEc5chE",
            "duration": "18m 40s",
            "difficulty": "Intermediate",
            "whyBest": "Official Redis walkthrough for semantic caching, useful for latency and cost reduction in LLM apps.",
            "skip": "None.",
            "id": "LRswXEc5chE"
          }
        ],
        "miniProject": "pgvector HNSW Search: Spin up Postgres with pgvector in Docker. Create a table `documents (id UUID, title TEXT, embedding vector(1536))` and run cosine similarity queries via Python `asyncpg`.",
        "prodProject": "Enterprise Semantic Caching & Conversational Memory Gateway: Build a Python middleware tier that intercepts incoming LLM requests, checks a Redis HNSW semantic cache for $>0.96$ vector similarity hits, serves cached answers instantly, or routes to OpenAI while storing conversational history in Redis session hashes.",
        "repos": [
          {
            "name": "pgvector/pgvector",
            "desc": "Open-source vector similarity search for PostgreSQL.",
            "url": "https://github.com/pgvector/pgvector"
          },
          {
            "name": "redis/redis-py",
            "desc": "Official Python client for Redis with native vector search support.",
            "url": "https://github.com/redis/redis-py"
          }
        ],
        "docs": [
          {
            "name": "pgvector HNSW Indexing Guide",
            "url": "https://github.com/pgvector/pgvector#hnsw"
          },
          {
            "name": "Redis Vector Search Documentation",
            "url": "https://redis.io/docs/latest/develop/interact/search-and-query/basic-constructs/vector-fields/"
          }
        ],
        "mistakes": [
          "Executing full table scans on vector columns without first creating HNSW indexes (CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)).",
          "Setting Redis cache eviction policies incorrectly, causing semantic cache keys to overwrite critical user session data."
        ],
        "interviewQA": [
          {
            "q": "What is the architectural advantage of PostgreSQL with pgvector over dedicated vector databases?",
            "a": "It eliminates dual-database synchronization bugs. You can execute ACID-compliant database transactions that insert relational customer orders AND document embeddings simultaneously, and write queries that join relational FOREIGN KEYS with vector similarity WHERE clauses in a single database round-trip."
          },
          {
            "q": "How does Redis Semantic Caching work mathematically?",
            "a": "When a query arrives, it is embedded into a vector V_q and searched against a Redis HNSW vector index of previously asked questions. If a stored question matches with Cosine Similarity > 0.95, the system bypasses calling OpenAI entirely and returns the cached answer string from Redis RAM in <5ms."
          }
        ],
        "checklist": [
          "Configured PostgreSQL with pgvector and created optimized HNSW vector indexes.",
          "Executed complex SQL queries combining relational WHERE clauses with `<->` cosine distance operators.",
          "Implemented a Redis semantic caching layer that reduces API token spend and latency."
        ]
      },
      "projectVideos": [
        {
          "title": "Build high-performance RAG using just PostgreSQL (Full Tutorial)",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=hAdEuDBN57g",
          "duration": "36m",
          "why": "Best pgvector-style project because it keeps retrieval inside PostgreSQL.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Postgres pgvector Extension - Vector Database with PostgreSQL / Langchain Integration",
          "creator": "BugBytes",
          "url": "https://www.youtube.com/watch?v=FDBnyJu_Ndg",
          "duration": "30m",
          "why": "Practical pgvector integration with LangChain.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "How to Build Semantic Caching for RAG: Cut LLM Costs by 90% & Boost Performance",
          "creator": "Data Mastery",
          "url": "https://www.youtube.com/watch?v=eTO1WfbtoXA",
          "duration": "34m",
          "why": "Practical Redis/semantic-cache use case for production AI economics.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 7,
      "name": "Embeddings And Semantic Search Fundamentals",
      "time": "Time: 10-12h. Cumulative: 76h.",
      "videos": [
        {
          "order": 1,
          "title": "Vector Databases simply explained! (Embeddings & Indexes)",
          "creator": "AssemblyAI",
          "url": "https://www.youtube.com/watch?v=dN0lsF2cvm4",
          "id": "dN0lsF2cvm4",
          "duration": "4m",
          "difficulty": "Easy",
          "why": "Best quick concept primer.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "How does a Vector Database work?",
          "creator": "KodeKloud",
          "url": "https://www.youtube.com/watch?v=VVNYQKDLY5s",
          "id": "VVNYQKDLY5s",
          "duration": "11m",
          "difficulty": "Easy",
          "why": "Explains indexing and similarity cleanly.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "Gemini Embedding 2 - Audio, Text, Images, Docs, Videos",
          "creator": "Sam Witteveen",
          "url": "https://www.youtube.com/watch?v=zUkKvWBJ_0I",
          "id": "zUkKvWBJ_0I",
          "duration": "21m",
          "difficulty": "Medium",
          "why": "Up-to-date multimodal embedding context.",
          "skip": "Skip modality demos you do not need."
        },
        {
          "order": 4,
          "title": "Cohere AI's LLM for Semantic Search in Python",
          "creator": "James Briggs",
          "url": "https://www.youtube.com/watch?v=ejpc-nbKY2Y",
          "id": "ejpc-nbKY2Y",
          "duration": "15m",
          "difficulty": "Medium",
          "why": "Strong semantic search coding pattern.",
          "skip": "Provider-specific parts optional."
        },
        {
          "order": 5,
          "title": "Metadata Filtering for Vector Search + Latest Filter Tech",
          "creator": "James Briggs",
          "url": "https://www.youtube.com/watch?v=H_kJDHvu-v8",
          "id": "H_kJDHvu-v8",
          "duration": "34m",
          "difficulty": "Medium",
          "why": "Covers metadata filters, a production retrieval requirement.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 4,
        "part": 1,
        "partName": "PART 1: Foundation, Embeddings & RAG",
        "title": "Embeddings And Semantic Search Fundamentals",
        "time": "8 Hours",
        "cumTime": "35h (15.9%)",
        "techs": [
          "Embeddings",
          "Cosine Similarity",
          "Matryoshka MRL",
          "Sentence-Transformers"
        ],
        "goal": "Understand the foundational data geometry of modern AI: Dense Vector Embeddings. Generate, normalize, store, and compare high-dimensional floating-point vectors using cloud and local open-source models.",
        "whyMatters": "SQL databases query by exact string matching or B-Tree value equivalency. Embeddings transform text into floating-point arrays where semantic meaning is represented by physical distance in multi-dimensional space.",
        "javaAnalogy": "An embedding vector is like a cryptographic hash of semantic meaning—instead of SHA-256 producing identical hashes for identical text, embeddings produce nearby coordinates for conceptual synonyms.",
        "videos": [
          {
            "step": "4.1",
            "title": "Vector Embeddings Explained — The Core of Vector DBs and RAG",
            "creator": "James Briggs",
            "url": "https://www.youtube.com/watch?v=5M8Cg8JtK9w",
            "duration": "40m",
            "difficulty": "Beginner",
            "whyBest": "Demystifies 1,536-dimensional space using intuitive visualizations. Explains how semantic concepts cluster together and how vector math operates.",
            "skip": "None. Watch every minute."
          },
          {
            "step": "4.2",
            "title": "OpenAI Embedding Models & Matryoshka Embeddings",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=ySus5ZS0b94",
            "duration": "30m",
            "difficulty": "Intermediate",
            "whyBest": "Explains Matryoshka Representation Learning (MRL)—how to truncate a 3,072-dim vector to 512 dimensions while retaining 99% accuracy, cutting DB RAM costs by 80%.",
            "skip": "Skip basic API key setup."
          },
          {
            "step": "4.3",
            "title": "Open Source HuggingFace Embeddings Locally with Sentence-Transformers",
            "creator": "Nicholas Renotte",
            "url": "https://www.youtube.com/watch?v=QdDoFfkVkcw",
            "duration": "35m",
            "difficulty": "Intermediate",
            "whyBest": "Shows how to run high-performance open-source embedding models locally using sentence-transformers, eliminating external API privacy risks and per-token costs.",
            "skip": "Skip PyTorch installation troubleshooting on Windows."
          }
        ],
        "miniProject": "Local Semantic Similarity Engine: Ingest 50 technical support tickets, generate 384-dim local embeddings, write a pure Python cosine similarity function, and match queries.",
        "prodProject": "Async Embedding Pipeline with Matryoshka Truncation: Batch-ingest 10,000 knowledge base articles into 1,024-dim truncated vectors with L2 normalization and Parquet disk checkpointing.",
        "repos": [
          {
            "name": "UKPLab/sentence-transformers",
            "desc": "Industry standard framework for local text embedding models.",
            "url": "https://github.com/UKPLab/sentence-transformers"
          },
          {
            "name": "qdrant/fastembed",
            "desc": "Fast ONNX-based Python embedding library without PyTorch bloat.",
            "url": "https://github.com/qdrant/fastembed"
          }
        ],
        "docs": [
          {
            "name": "MTEB Embedding Benchmark Leaderboard",
            "url": "https://huggingface.co/spaces/mteb/leaderboard"
          },
          {
            "name": "OpenAI Embeddings Guide",
            "url": "https://platform.openai.com/docs/guides/embeddings"
          }
        ],
        "mistakes": [
          "Using different embedding models for indexing documents and searching queries (causes dimension mismatch or gibberish similarity).",
          "Comparing un-normalized vectors using Dot Product without prior L2 normalization."
        ],
        "interviewQA": [
          {
            "q": "What is the mathematical difference between Cosine Similarity and Dot Product, and when should you choose each?",
            "a": "Cosine similarity measures angle regardless of magnitude; Dot product multiplies corresponding components. Enterprise rule: If vectors are L2-normalized (length=1.0), Cosine and Dot Product yield identical rankings, but Dot Product executes up to 3x faster on CPUs because it skips square-root division."
          },
          {
            "q": "How does Matryoshka Representation Learning (MRL) optimize cloud vector database costs?",
            "a": "MRL trains embedding neural networks with nested loss functions, packing critical semantic coarse concepts into early dimensions (first 256/512). Truncating a 3,072-dim vector to 512 dimensions reduces database RAM requirements by 83% while retaining >98.5% semantic accuracy."
          }
        ],
        "checklist": [
          "Generated vectors using OpenAI cloud API and local HuggingFace models.",
          "Implemented raw Python functions for Cosine Similarity and Dot Product.",
          "Built an automated token chunking algorithm using tiktoken with overlapping windows."
        ]
      },
      "projectVideos": [
        {
          "title": "Text Embeddings, Classification, and Semantic Search (w/ Python Code)",
          "creator": "Shaw Talebi",
          "url": "https://www.youtube.com/watch?v=sNa_uiqSlJo",
          "duration": "31m",
          "why": "Practical embeddings project that teaches semantic search without jumping too early into full RAG frameworks.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Semantic Search with LangChain, OpenAI LLMs & FAISS: From Zero to Hero!",
          "creator": "Onur Baltaci",
          "url": "https://www.youtube.com/watch?v=1zkpT0-QKjk",
          "duration": "23m",
          "why": "Useful follow-along to connect embeddings, indexes, and user-facing search.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 8,
      "name": "Vector Databases: Pinecone, Chroma, Qdrant, Weaviate, FAISS",
      "time": "Time: 10-12h. Cumulative: 88h.",
      "videos": [
        {
          "order": 1,
          "title": "Getting started with Pinecone monthly webinar (November 2025)",
          "creator": "Pinecone",
          "url": "https://www.youtube.com/watch?v=pY_7RSUnotk",
          "id": "pY_7RSUnotk",
          "duration": "42m",
          "difficulty": "Medium",
          "why": "Official, current Pinecone onboarding.",
          "skip": "Skip marketing intro."
        },
        {
          "order": 2,
          "title": "How to Build a Local AI Agent With Python (Ollama, LangChain & RAG)",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=E4l91XKQSgw",
          "id": "E4l91XKQSgw",
          "duration": "28m",
          "difficulty": "Medium",
          "why": "Practical local Chroma/LangChain pattern.",
          "skip": "Ollama details optional."
        },
        {
          "order": 3,
          "title": "Let's Build a Local RAG System with Ollama & Qdrant",
          "creator": "Maximilian Schwarzmuller Extended",
          "url": "https://www.youtube.com/watch?v=6diVTn3J7QE",
          "id": "6diVTn3J7QE",
          "duration": "2h 1m",
          "difficulty": "Medium",
          "why": "Solid Qdrant hands-on.",
          "skip": "Skip Ollama if using cloud LLMs."
        },
        {
          "order": 4,
          "title": "How to Build a RAG App with LangChain, Llama 3.1, and ChromaDB",
          "creator": "Data Engineer Academy",
          "url": "https://www.youtube.com/watch?v=Bq6uhc27sPY",
          "id": "Bq6uhc27sPY",
          "duration": "1h 7m",
          "difficulty": "Medium",
          "why": "Practical Chroma app implementation.",
          "skip": "Local model details optional."
        },
        {
          "order": 5,
          "title": "FAISS Vector Library with LangChain and OpenAI (Semantic Search)",
          "creator": "Ryan & Matt Data Science",
          "url": "https://www.youtube.com/watch?v=ZCSsIkyCZk4",
          "id": "ZCSsIkyCZk4",
          "duration": "20m",
          "difficulty": "Medium",
          "why": "Good FAISS semantic search intro.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 5,
        "part": 1,
        "partName": "PART 1: Foundation, Embeddings & RAG",
        "title": "Vector Databases: Pinecone, Chroma, Qdrant, Weaviate, FAISS",
        "time": "15 Hours",
        "cumTime": "50h (22.7%)",
        "techs": [
          "Pinecone",
          "Chroma",
          "Qdrant",
          "Weaviate",
          "FAISS",
          "HNSW / IVF"
        ],
        "goal": "Master the storage, indexing, and querying engines of AI. Understand HNSW graphs and IVF indexes. Deploy, configure, and benchmark Pinecone, ChromaDB, Qdrant, Weaviate, and FAISS with pre-filtering metadata.",
        "whyMatters": "Iterating through 50 million vectors for exact brute-force similarity takes several seconds. Vector databases use specialized HNSW graphs to achieve sub-10-millisecond approximate nearest neighbor (ANN) lookups.",
        "javaAnalogy": "Vector DBs are your Spring Data JPA / Hibernate persistence tier. HNSW indexes are the high-dimensional equivalent of PostgreSQL B-Tree indexes.",
        "videos": [
          {
            "step": "5.1",
            "title": "Vector Database Fundamentals — HNSW, IVF, and ANN Search Algorithms",
            "creator": "James Briggs",
            "url": "https://www.youtube.com/watch?v=QvKMwLWfw2o",
            "duration": "45m",
            "difficulty": "Advanced",
            "whyBest": "Breaks down how HNSW builds multi-layer skip-lists across vector space, balancing search latency against recall accuracy and memory consumption.",
            "skip": "None. Essential computer science architecture."
          },
          {
            "step": "5.2",
            "title": "Qdrant Vector Database Complete Crash Course",
            "creator": "Nicholas Renotte",
            "url": "https://www.youtube.com/watch?v=wH4w3f2n47Y",
            "duration": "50m",
            "difficulty": "Intermediate",
            "whyBest": "Demonstrates spinning up Rust-based Qdrant in Docker, ingesting payloads, and performing hybrid filtering (vector similarity + SQL-like metadata conditions).",
            "skip": "Skip basic Docker Compose setup if familiar."
          },
          {
            "step": "5.3",
            "title": "Pinecone Serverless Complete Guide for Developers",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=1d1w8b3t424",
            "duration": "35m",
            "difficulty": "Intermediate",
            "whyBest": "Explains Pinecone's serverless cloud architecture (separating compute from S3 storage), namespace isolation for SaaS multi-tenancy, and live indexing.",
            "skip": "None."
          },
          {
            "step": "5.4",
            "title": "ChromaDB & Weaviate — Local vs Embedded Vector Databases",
            "creator": "Sam Witteveen",
            "url": "https://www.youtube.com/watch?v=gJg28t4w3e4",
            "duration": "40m",
            "difficulty": "Beginner",
            "whyBest": "Pragmatic comparison of ChromaDB (lightweight embedded DB like H2 or SQLite) versus Weaviate (enterprise GraphQL-driven vector DB).",
            "skip": "Skip overview slides; focus on Python syntax."
          }
        ],
        "miniProject": "Multi-Tenant Product Search: Ingest 500 mock products into Qdrant with tenant_id payload tags. Execute a pre-filtered semantic search requiring strict tenant matching.",
        "prodProject": "Enterprise Vector DB Benchmarking Suite: Ingest 10,000 vectors into Qdrant and Pinecone, enable int8 scalar quantization, and generate an automated P95 latency vs Recall@10 report.",
        "repos": [
          {
            "name": "qdrant/qdrant",
            "desc": "High-performance Rust vector database with scalar quantization.",
            "url": "https://github.com/qdrant/qdrant"
          },
          {
            "name": "weaviate/weaviate",
            "desc": "Cloud-native AI database with GraphQL and hybrid BM25.",
            "url": "https://github.com/weaviate/weaviate"
          }
        ],
        "docs": [
          {
            "name": "Qdrant Documentation & HNSW Tuning",
            "url": "https://qdrant.tech/documentation/"
          },
          {
            "name": "Pinecone Serverless Docs",
            "url": "https://docs.pinecone.io/guides/get-started/overview"
          }
        ],
        "mistakes": [
          "Performing post-filtering in Python (if item['tenant'] == 'acme') after vector retrieval instead of database pre-filtering.",
          "Failing to enable int8 scalar quantization on datasets over 1 million vectors, causing massive RAM bills."
        ],
        "interviewQA": [
          {
            "q": "How does Hierarchical Navigable Small World (HNSW) achieve sub-millisecond approximate nearest neighbor search?",
            "a": "HNSW builds multi-layered skip-list graphs. Top layers have sparse nodes with long links for rapid structural leaps across space. Lower layers contain denser neighborhoods for fine-grained convergence. Trade-off: O(log N) speed and 98% recall versus higher RAM consumption for pointer storage."
          },
          {
            "q": "How do you isolate customer data in a multi-tenant vector database to guarantee zero data leakage?",
            "a": "Use Namespace / Payload Partitioning. All tenants share one large HNSW index, but every vector stores an immutable tenant_id payload. The backend API injects a mandatory pre-filter (filter: {'tenant_id': 'current_user'}) into every query during graph traversal."
          }
        ],
        "checklist": [
          "Deployed local Qdrant/ChromaDB via Docker and connected via async Python client.",
          "Implemented hybrid pre-filtering combining semantic similarity with SQL-style metadata filters.",
          "Executed int8 scalar quantization, verifying 75% memory footprint reduction."
        ]
      },
      "projectVideos": [
        {
          "title": "Chatbots with RAG: LangChain Full Walkthrough",
          "creator": "James Briggs",
          "url": "https://www.youtube.com/watch?v=LhnCsygAvzY",
          "duration": "36m",
          "why": "Strong Pinecone/RAG walkthrough from one of the best vector-search educators.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "4-Langchain Series-Getting Started With RAG Pipeline Using Langchain Chromadb And FAISS",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=9Thc6hRw2Gs",
          "duration": "30m",
          "why": "Practical comparison-style build using ChromaDB and FAISS.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Get Started with Qdrant Vector Database: Build your First RAG (Part 1)",
          "creator": "AI Anytime",
          "url": "https://www.youtube.com/watch?v=7DStwsEj7rA",
          "duration": "47m",
          "why": "Hands-on Qdrant project for learning a production-grade open-source vector DB.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Supercharge Retrieval-Augmented Generation (RAG) with Weaviate in Python!",
          "creator": "Dylan Humphreys",
          "url": "https://www.youtube.com/watch?v=gLYuxuBycAU",
          "duration": "36m",
          "why": "Focused Weaviate implementation project for comparing vector database choices.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 9,
      "name": "RAG Fundamentals",
      "time": "Time: 10-12h. Cumulative: 100h.",
      "videos": [
        {
          "order": 1,
          "title": "Learn RAG From Scratch - Python AI Tutorial from a LangChain Engineer",
          "creator": "freeCodeCamp.org",
          "url": "https://www.youtube.com/watch?v=sVcwVQRHIc8",
          "id": "sVcwVQRHIc8",
          "duration": "2h 33m",
          "difficulty": "Medium",
          "why": "Best long-form practical foundation for building a first RAG pipeline before advanced retrieval techniques.",
          "skip": "Skip LangChain basics only if you already completed the framework module."
        },
        {
          "order": 2,
          "title": "RAG Explained in 12 Minutes",
          "creator": "Aishwarya Srinivasan",
          "url": "https://www.youtube.com/watch?v=v0ynfDPpe4E",
          "id": "v0ynfDPpe4E",
          "duration": "12m",
          "difficulty": "Easy",
          "why": "Crisp mental model.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "RAG with Mistral AI!",
          "creator": "James Briggs",
          "url": "https://www.youtube.com/watch?v=I0c405L7-9A",
          "id": "I0c405L7-9A",
          "duration": "12m",
          "difficulty": "Medium",
          "why": "Shows provider-independent RAG pattern.",
          "skip": "Provider setup optional."
        },
        {
          "order": 4,
          "title": "Gemini RAG - File Search Tool",
          "creator": "Sam Witteveen",
          "url": "https://www.youtube.com/watch?v=MuP9ki6Bdtg",
          "id": "MuP9ki6Bdtg",
          "duration": "25m",
          "difficulty": "Medium",
          "why": "Covers Gemini-native retrieval option.",
          "skip": "Skip if not using Gemini yet."
        },
        {
          "order": 5,
          "title": "How to Get Your Data Ready for AI Agents (Docs, PDFs, Websites)",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=9lBTS5dM27c",
          "id": "9lBTS5dM27c",
          "duration": "25m",
          "difficulty": "Medium",
          "why": "Great production ingestion mindset.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 6,
        "part": 1,
        "partName": "PART 1: Foundation, Embeddings & RAG",
        "title": "RAG Fundamentals",
        "time": "12 Hours",
        "cumTime": "62h (28.2%)",
        "techs": [
          "RAG Fundamentals",
          "Document Parsing",
          "Semantic Chunking",
          "Citations"
        ],
        "goal": "Master end-to-end RAG architecture. Build document ingestion pipelines parsing messy PDFs/HTML, apply advanced semantic chunking, and construct retrieval prompts with strict source citation verification.",
        "whyMatters": "LLMs know zero about your internal HR policies or private Java microservices. Fine-tuning on internal data is expensive and non-auditable. RAG decouples knowledge storage from reasoning and is the #1 enterprise AI pattern.",
        "javaAnalogy": "RAG is like injecting a dynamic Spring Cache or Hibernate Level-2 query result directly into a method parameter before executing domain logic.",
        "videos": [
          {
            "step": "6.1",
            "title": "Retrieval-Augmented Generation (RAG) Complete Architecture Deep Dive",
            "creator": "James Briggs",
            "url": "https://www.youtube.com/watch?v=T-D1OfcDW1M",
            "duration": "50m",
            "difficulty": "Intermediate",
            "whyBest": "Builds a complete RAG system from scratch without heavy orchestration wrappers. Demystifies the ingestion loop vs the generation retrieval loop.",
            "skip": "None. Watch at normal speed."
          },
          {
            "step": "6.2",
            "title": "Document Chunking Strategies for RAG — Fixed, Semantic & Markdown",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=8OJC21T2SL4",
            "duration": "40m",
            "difficulty": "Intermediate",
            "whyBest": "Explains why standard character splitting destroys context. Demonstrates Recursive Character, Markdown Header-aware, and Semantic embedding-based chunking.",
            "skip": "Skip introductory RAG definitions; jump to chunking code."
          },
          {
            "step": "6.3",
            "title": "Building Production RAG Systems — LangChain vs LlamaIndex vs Pure Python",
            "creator": "Sam Witteveen",
            "url": "https://www.youtube.com/watch?v=TRjq7t2Ms5I",
            "duration": "35m",
            "difficulty": "Intermediate",
            "whyBest": "Objective evaluation of raw Python vs LlamaIndex. Shows how to handle document metadata and citations so your backend returns clickable source URLs.",
            "skip": "None."
          }
        ],
        "miniProject": "Internal Policy RAG CLI: Ingest 3 company PDFs using recursive character splitting, store in ChromaDB, and generate answers with exact filename/section citations.",
        "prodProject": "Multimodal & Table-Aware RAG Pipeline: Integrate LlamaParse/Unstructured to preserve PDF table formatting as Markdown, implement Parent-Child small-to-big indexing, and enforce Pydantic citation checks.",
        "repos": [
          {
            "name": "run-llama/llama_index",
            "desc": "Leading enterprise data framework for document indexing and retrieval.",
            "url": "https://github.com/run-llama/llama_index"
          },
          {
            "name": "Unstructured-IO/unstructured",
            "desc": "Open-source document extraction library for complex PDFs and HTML.",
            "url": "https://github.com/Unstructured-IO/unstructured"
          }
        ],
        "docs": [
          {
            "name": "LlamaIndex Understanding RAG",
            "url": "https://docs.llamaindex.ai/en/stable/understanding/rag/"
          },
          {
            "name": "Unstructured.io API Docs",
            "url": "https://docs.unstructured.io/"
          }
        ],
        "mistakes": [
          "Splitting text across table boundaries or code blocks, causing the LLM to hallucinate incorrect numbers.",
          "Missing document metadata (The 'Blind Chunk' problem), making it impossible to cite source page numbers."
        ],
        "interviewQA": [
          {
            "q": "Why is RAG superior to LLM fine-tuning for proprietary internal enterprise knowledge?",
            "a": "Fine-tuning encodes facts into static parametric weights which take days/hours to retrain and cannot enforce user-level access permissions. RAG decouples knowledge storage from reasoning: updating a PDF reflects instantly, and SQL/RBAC pre-filters ensure users only retrieve documents they are permitted to view."
          },
          {
            "q": "Explain the Parent-Child (Small-to-Big) document retrieval pattern.",
            "a": "Embeddings work best on small 300-token chunks (high precision), but LLMs need broad context (1,500 tokens) to synthesize answers. Parent-Child indexes small child chunks in the vector DB with a pointer to the large parent document. When a query hits the small child vector, your backend fetches and passes the full 1,500-token parent to the LLM."
          }
        ],
        "checklist": [
          "Built an end-to-end RAG ingestion pipeline from scratch using Python and embedded DBs.",
          "Implemented structure-aware markdown/table parsing that preserves tabular alignment.",
          "Deployed Parent-Child retrieval where small vector hits return full parent context blocks."
        ]
      },
      "projectVideos": [
        {
          "title": "RAG + Langchain Python Project: Easy AI/Chat For Your Docs",
          "creator": "pixegami",
          "url": "https://www.youtube.com/watch?v=tcqEUSNCn8I",
          "duration": "17m",
          "why": "Compact document-chat project for a quick first RAG implementation.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "cost": "Free/local or low-cost API; cloud optional where noted",
          "title": "Build a Complete Medical Chatbot with LLMs, LangChain, Pinecone, Flask & AWS",
          "creator": "DSwithBappy",
          "url": "https://www.youtube.com/watch?v=KnoVFU0yCUc",
          "duration": "2h 51m",
          "why": "Large real-world RAG chatbot use case with documents, vector search, app backend, and deployment context."
        },
        {
          "title": "Build a Customer Support Chatbot That Doesn't Make Stuff Up (RAG Tutorial)",
          "creator": "ssktechy",
          "url": "https://www.youtube.com/watch?v=B6pjngFfg6Q",
          "duration": "21m",
          "why": "Useful customer-support use case that maps directly to enterprise knowledge-base products.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ],
      "supplementalTracks": [
        {
          "id": "document-intelligence-multimodal-ingestion",
          "title": "Enterprise Document Intelligence, OCR, Speech And Multimodal RAG",
          "time": "Optional: 4-6h",
          "goal": "Learn how enterprise RAG systems ingest real customer content: scanned PDFs, forms, invoices, images, tables, video/audio transcripts, and multimodal documents.",
          "whyMatters": "AI Engineer certifications and customer projects often include knowledge mining, document intelligence, OCR, speech, and multimodal ingestion. This track fills that gap without turning the main path into a computer-vision or speech course.",
          "techs": [
            "Document Intelligence",
            "OCR",
            "Speech-to-text",
            "Multimodal RAG",
            "PDF tables",
            "Enterprise ingestion"
          ],
          "videos": [
            {
              "step": "9.A",
              "title": "Multimodal RAG: Chat with PDFs (Images & Tables) [2025]",
              "creator": "Alejandro AO",
              "url": "https://www.youtube.com/watch?v=uLrReyH5cu0",
              "duration": "1h 11m 4s",
              "difficulty": "Intermediate",
              "whyBest": "Best fit for enterprise knowledge bases that must handle PDFs with tables and images instead of plain text only.",
              "skip": "Skip UI polish and focus on parsing, chunking, retrieval, and multimodal context."
            },
            {
              "step": "9.B",
              "title": "Document Parsing Using Azure Document Intelligence",
              "creator": "Sai Ram Penjarla",
              "url": "https://www.youtube.com/watch?v=MP-hDgjYbCY",
              "duration": "15m 50s",
              "difficulty": "Intermediate",
              "whyBest": "Practical Azure Document Intelligence parsing walkthrough that maps to AI-102 document extraction and enterprise forms use cases.",
              "skip": "None."
            },
            {
              "step": "9.C",
              "title": "Google Document AI: Build OCR & Form Parser Processors",
              "creator": "Cloud Guru Certification",
              "url": "https://www.youtube.com/watch?v=pIGNfFpobzs",
              "duration": "19m 25s",
              "difficulty": "Intermediate",
              "whyBest": "Shows Google Cloud Document AI OCR and form parser workflow for cloud-native document extraction.",
              "skip": "Skip console-only recap if you already understand processors."
            },
            {
              "step": "9.D",
              "title": "Extract Data from Documents/Images Using AWS Textract | Document Processing MVP",
              "creator": "CodersArts",
              "url": "https://www.youtube.com/watch?v=guHg9sREBr8",
              "duration": "10m 6s",
              "difficulty": "Intermediate",
              "whyBest": "AWS-side document extraction reference for customers who standardize on AWS and need Textract before Bedrock/RAG.",
              "skip": "None."
            },
            {
              "step": "9.E",
              "title": "Build a Python Speech to Text App Using OpenAI Whisper API and Streamlit",
              "creator": "Onur Baltaci",
              "url": "https://www.youtube.com/watch?v=GRxpcGUCz_s",
              "duration": "13m 49s",
              "difficulty": "Beginner",
              "whyBest": "Quick practical speech-to-text ingestion path for call-center, meeting, and support-ticket AI systems.",
              "skip": "Skip Streamlit UI details if you only need transcription ingestion."
            }
          ]
        }
      ]
    },
    {
      "num": 10,
      "name": "Advanced RAG, Hybrid Search And Re-ranking",
      "time": "Time: 10-12h. Cumulative: 112h.",
      "videos": [
        {
          "order": 1,
          "title": "Advanced RAG 03 - Hybrid Search BM25 & Ensembles",
          "creator": "Sam Witteveen",
          "url": "https://www.youtube.com/watch?v=lYxGYXjfrNI",
          "id": "lYxGYXjfrNI",
          "duration": "7m",
          "difficulty": "Medium",
          "why": "Compact hybrid search explanation.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "The Complete Guide to Hybrid Search in RAG (BM25 + Embeddings + Reranker)",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=XvKiTfd6Xvo",
          "id": "XvKiTfd6Xvo",
          "duration": "59m",
          "difficulty": "Advanced",
          "why": "Best project-quality explanation of dense plus keyword search and reranking in one workflow.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "RAG But Better: Rerankers with Cohere AI",
          "creator": "James Briggs",
          "url": "https://www.youtube.com/watch?v=Uh9bYiVrW_s",
          "id": "Uh9bYiVrW_s",
          "duration": "24m",
          "difficulty": "Medium",
          "why": "Practical reranking implementation.",
          "skip": "Cohere specifics optional."
        },
        {
          "order": 4,
          "title": "LangChain Multi-Query Retriever for RAG",
          "creator": "James Briggs",
          "url": "https://www.youtube.com/watch?v=VFf8XJUIHnU",
          "id": "VFf8XJUIHnU",
          "duration": "19m",
          "difficulty": "Medium",
          "why": "Production-relevant query expansion.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "Advanced RAG techniques for developers",
          "creator": "Google Cloud Tech",
          "url": "https://www.youtube.com/watch?v=sGvXO7CVwc0",
          "id": "sGvXO7CVwc0",
          "duration": "8m",
          "difficulty": "Medium",
          "why": "Cloud/enterprise advanced RAG overview.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 7,
        "part": 1,
        "partName": "PART 1: Foundation, Embeddings & RAG",
        "title": "Advanced RAG, Hybrid Search And Re-ranking",
        "time": "13 Hours",
        "cumTime": "75h (34.1%)",
        "techs": [
          "Advanced RAG",
          "Hybrid Search (BM25+Dense)",
          "Re-ranking",
          "Cohere / BGE",
          "CRAG / HyDE"
        ],
        "goal": "Implement Hybrid Search combining dense vector similarity with sparse BM25 lexical matching via Reciprocal Rank Fusion (RRF). Deploy Cross-Encoder Re-ranking models and implement Corrective RAG (CRAG) loops.",
        "whyMatters": "Pure vector search fails when searching for exact error codes (ERR-74205) or version numbers. Hybrid search + Cross-Encoder re-ranking boosts retrieval precision by 35% and reduces context noise by 70%.",
        "javaAnalogy": "Hybrid search is like querying both Elasticsearch full-text index and PostgreSQL relational database in parallel, then using a custom Java comparator to sort the combined top-10 results.",
        "videos": [
          {
            "step": "7.3",
            "title": "Advanced RAG 05 - HyDE - Hypothetical Document Embeddings",
            "creator": "Sam Witteveen",
            "url": "https://www.youtube.com/watch?v=v_BnBEubv58",
            "duration": "11m 53s",
            "difficulty": "Advanced",
            "whyBest": "Concise implementation-focused explanation of HyDE query transformation for better retrieval.",
            "skip": "None.",
            "id": "v_BnBEubv58"
          }
        ],
        "miniProject": "Hybrid Search with RRF Fusion: Build a Qdrant search module with dense and sparse spaces. Query error codes to prove BM25 catches exact strings while dense catches concepts.",
        "prodProject": "Corrective RAG (CRAG) with Cross-Encoder Re-ranking: Build a two-stage retrieval pipeline with multi-query expansion, Cohere Cross-Encoder re-ranking, and an LLM-as-a-Judge fallback to live Tavily web search.",
        "repos": [
          {
            "name": "cohere-ai/cohere-python",
            "desc": "Official SDK for industry-leading cross-encoder re-ranking models.",
            "url": "https://github.com/cohere-ai/cohere-python"
          },
          {
            "name": "tavily-ai/tavily-python",
            "desc": "Search API built for RAG fallback pipelines returning clean markdown.",
            "url": "https://github.com/tavily-ai/tavily-python"
          }
        ],
        "docs": [
          {
            "name": "Pinecone Hybrid Search Guide",
            "url": "https://docs.pinecone.io/guides/data/understand-hybrid-search"
          },
          {
            "name": "Cohere Rerank Documentation",
            "url": "https://docs.cohere.com/docs/rerank-overview"
          }
        ],
        "mistakes": [
          "Trying to linearly combine unnormalized BM25 scores (0 to 50+) with Cosine similarities (0.7 to 0.95) without using Reciprocal Rank Fusion (RRF).",
          "Running heavy Cross-Encoder re-ranking across the entire database instead of only the top 30 retrieved candidates."
        ],
        "interviewQA": [
          {
            "q": "What is the architectural difference between a Bi-Encoder and a Cross-Encoder?",
            "a": "A Bi-Encoder embeds query and document separately into static vectors for fast O(log N) dot-product indexing. A Cross-Encoder takes query and document concatenated together ([CLS] query [SEP] doc) through transformer attention layers, scoring contextual relevance from 0 to 1 with human-like accuracy, but requiring O(N) compute per pair."
          },
          {
            "q": "Why is Reciprocal Rank Fusion (RRF) superior to linear score weighting?",
            "a": "Because BM25 TF-IDF scores and Cosine similarities have completely incompatible score distributions. RRF ignores raw scores entirely and sums reciprocal ordinal rank positions: 1/(k + rank_dense) + 1/(k + rank_sparse), preventing score normalization bugs."
          }
        ],
        "checklist": [
          "Configured simultaneous dense semantic search and sparse BM25 search in Qdrant.",
          "Written a clean Python implementation of Reciprocal Rank Fusion (RRF).",
          "Integrated Cohere Rerank API into a two-stage pipeline with automated relevance grading."
        ]
      },
      "projectVideos": [
        {
          "cost": "Free/local or low-cost API; cloud optional where noted",
          "title": "Make LLM Agents Faster and Cheaper with Semantic Caching & Reranking",
          "creator": "AI RoundTable",
          "url": "https://www.youtube.com/watch?v=TlMNI0hTtYU",
          "duration": "1h 18m",
          "why": "Advanced retrieval/use-case optimization with semantic caching and reranking for production agents."
        },
        {
          "cost": "Free/local or low-cost API; cloud optional where noted",
          "title": "Build a Real Estate RAG Chatbot with Qdrant + LangChain (Full Tutorial)",
          "creator": "AI with Adeel",
          "url": "https://www.youtube.com/watch?v=LUdYJZvEdL4",
          "duration": "42m",
          "why": "Applied RAG project with a specific domain, useful for practicing query quality and retrieval tuning."
        }
      ]
    },
    {
      "num": 11,
      "name": "LangChain And LlamaIndex",
      "time": "Time: 10-12h. Cumulative: 124h.",
      "videos": [
        {
          "order": 1,
          "title": "LangChain vs LangGraph vs LangSmith",
          "creator": "codebasics",
          "url": "https://www.youtube.com/watch?v=vJOGC8QJZJQ",
          "id": "vJOGC8QJZJQ",
          "duration": "10m",
          "difficulty": "Easy",
          "why": "Clarifies ecosystem roles.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Agentic AI Crash Course using LangChain",
          "creator": "codebasics",
          "url": "https://www.youtube.com/watch?v=D74el9mvNak",
          "id": "D74el9mvNak",
          "duration": "2h 24m",
          "difficulty": "Medium",
          "why": "Practical LangChain walkthrough.",
          "skip": "Skip repeated setup."
        },
        {
          "order": 3,
          "title": "Introduction to LlamaIndex with Python (2025)",
          "creator": "Alejandro AO",
          "url": "https://www.youtube.com/watch?v=cCyYGYyCka4",
          "id": "cCyYGYyCka4",
          "duration": "40m",
          "difficulty": "Medium",
          "why": "Current LlamaIndex onboarding.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "End to end RAG LLM App Using Llamaindex and OpenAI",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=hH4WkgILUD4",
          "id": "hH4WkgILUD4",
          "duration": "27m",
          "difficulty": "Medium",
          "why": "Fast LlamaIndex RAG path.",
          "skip": "Skip Streamlit polish."
        },
        {
          "order": 5,
          "title": "Agentic Document Processing with LlamaCloud",
          "creator": "LlamaIndex",
          "url": "https://www.youtube.com/watch?v=6q0jMcdbijQ",
          "id": "6q0jMcdbijQ",
          "duration": "53m",
          "difficulty": "Advanced",
          "why": "Official advanced document processing context.",
          "skip": "Skip product-specific pricing."
        }
      ],
      "desktop": {
        "id": 8,
        "part": 2,
        "partName": "PART 2: Orchestration, Agents & Workflows",
        "title": "LangChain And LlamaIndex",
        "time": "12 Hours",
        "cumTime": "87h (39.5%)",
        "techs": [
          "LangChain",
          "LlamaIndex",
          "LCEL",
          "Data Connectors",
          "Query Engines"
        ],
        "goal": "Master the two dominant enterprise orchestration frameworks. Understand when to use pure SDKs vs LangChain Expression Language (LCEL) or LlamaIndex data ingestion pipelines, avoiding framework bloat.",
        "whyMatters": "Just as Spring Boot provides abstractions over raw Servlets and JDBC, LangChain and LlamaIndex provide pre-built adapters for 100+ LLMs, 50+ vector DBs, and 300+ data sources, accelerating enterprise integration.",
        "javaAnalogy": "LangChain is your Spring Framework (dependency injection and chains for AI). LlamaIndex is your Spring Data / Hibernate (specialized data indexing and query retrieval pipelines).",
        "videos": [
          {
            "step": "8.1",
            "title": "LangChain Crash Course for Beginners & Intermediate Developers",
            "creator": "Nicholas Renotte",
            "url": "https://www.youtube.com/watch?v=lG7Uxts9SXs",
            "duration": "1h 10m",
            "difficulty": "Intermediate",
            "whyBest": "Covers modern LangChain architecture (LCEL pipe operator `|`, Runnables, prompt templates, and output parsers) without relying on deprecated LLMChain classes.",
            "skip": "Skip introductory installation slides."
          },
          {
            "step": "8.2",
            "title": "LlamaIndex Complete Guide — Enterprise Data Framework for LLMs",
            "creator": "Sam Witteveen",
            "url": "https://www.youtube.com/watch?v=eB1z3456789",
            "duration": "50m",
            "difficulty": "Intermediate",
            "whyBest": "Explains why LlamaIndex is superior to LangChain for complex document hierarchies, SubQuestionQueryEngines, and routing queries across SQL tables and vector stores.",
            "skip": "None."
          },
          {
            "step": "8.3",
            "title": "Building Agentic RAG From Scratch in Pure Python",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=RxwjoegpI98",
            "duration": "27m 28s",
            "difficulty": "Intermediate",
            "whyBest": "Shows when simple Python orchestration is enough before reaching for heavier frameworks.",
            "skip": "None.",
            "id": "RxwjoegpI98"
          }
        ],
        "miniProject": "LCEL Declarative Pipeline: Build a clean LangChain Expression Language pipeline (`prompt | model | parser`) that translates user bug reports into structured Jira JSON tickets.",
        "prodProject": "Enterprise Multi-Source Query Router: Use LlamaIndex to ingest a PostgreSQL relational database and an unstructured vector document store, automatically routing user questions to the correct engine via RouterQueryEngine.",
        "repos": [
          {
            "name": "langchain-ai/langchain",
            "desc": "The foundational framework for building LLM applications.",
            "url": "https://github.com/langchain-ai/langchain"
          },
          {
            "name": "run-llama/llama_index",
            "desc": "Leading data framework for connecting enterprise sources to LLMs.",
            "url": "https://github.com/run-llama/llama_index"
          }
        ],
        "docs": [
          {
            "name": "LangChain LCEL Conceptual Guide",
            "url": "https://python.langchain.com/docs/concepts/lcel/"
          },
          {
            "name": "LlamaIndex Query Engine Docs",
            "url": "https://docs.llamaindex.ai/en/stable/module_guides/deploying/query_engine/"
          }
        ],
        "mistakes": [
          "Using legacy LangChain classes like LLMChain or RetrievalQA which were deprecated in v0.2.",
          "Over-wrapping simple single-prompt REST calls in 10 layers of framework abstraction."
        ],
        "interviewQA": [
          {
            "q": "What is LangChain Expression Language (LCEL) and how does it improve async streaming?",
            "a": "LCEL is a declarative orchestration protocol using Unix-style pipe operators (|). It automatically implements async streaming (astream), parallel execution (RunnableParallel), and fallback circuits across every component in the chain."
          },
          {
            "q": "When should an architect choose LlamaIndex over LangChain?",
            "a": "Choose LlamaIndex when the primary architectural complexity is document data ingestion, hierarchical node parsing, and multi-database retrieval routing. Choose LangChain when building conversational agent tools and multi-step reasoning workflows."
          }
        ],
        "checklist": [
          "Constructed declarative LCEL chains with custom RunnableLambdas and async streaming.",
          "Built a LlamaIndex SubQuestionQueryEngine that breaks complex user questions into sub-queries.",
          "Documented architectural trade-offs between pure SDK code and orchestration frameworks."
        ]
      },
      "projectVideos": [
        {
          "title": "Complete RAG Crash Course With Langchain In 2 Hours",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=o126p1QN_RI",
          "duration": "2h 8m",
          "why": "Project-heavy LangChain build that turns framework concepts into a working RAG app.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Build Your Own Auto-GPT Apps with LangChain (Python Tutorial)",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=NYSWn1ipbgg",
          "duration": "30m",
          "why": "Practical LangChain app build from a strong AI engineering creator.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Build a RAG pipeline in LlamaIndex (simple)",
          "creator": "AWS Developers",
          "url": "https://www.youtube.com/watch?v=vNpxWaVzky8",
          "duration": "10m",
          "why": "Short official-style LlamaIndex build to reinforce framework differences.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 12,
      "name": "LangGraph And Model Context Protocol (MCP)",
      "time": "Time: 10-12h. Cumulative: 136h.",
      "videos": [
        {
          "order": 1,
          "title": "Building Effective Agents with LangGraph",
          "creator": "LangChain",
          "url": "https://www.youtube.com/watch?v=aHCDrAbH_go",
          "id": "aHCDrAbH_go",
          "duration": "32m",
          "difficulty": "Medium",
          "why": "Official practical agent architecture.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "LangGraph Complete Course for Beginners - Complex AI Agents with Python",
          "creator": "freeCodeCamp.org",
          "url": "https://www.youtube.com/watch?v=jGg_1h0qzaM",
          "id": "jGg_1h0qzaM",
          "duration": "3h 10m",
          "difficulty": "Medium",
          "why": "Deep hands-on graph course.",
          "skip": "Watch selected chapters as needed."
        },
        {
          "order": 3,
          "title": "Model Context Protocol Clearly Explained",
          "creator": "codebasics",
          "url": "https://www.youtube.com/watch?v=tzrwxLNHtRY",
          "id": "tzrwxLNHtRY",
          "duration": "15m",
          "difficulty": "Easy",
          "why": "Clear MCP concept primer.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Intro to MCP Servers - Model Context Protocol with Python Course",
          "creator": "freeCodeCamp.org",
          "url": "https://www.youtube.com/watch?v=DosHnyq78xY",
          "id": "DosHnyq78xY",
          "duration": "1h 13m",
          "difficulty": "Medium",
          "why": "Best Python MCP build path.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "MCP Crash Course: What Python Developers Need to Know",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=5xqFjh56AwM",
          "id": "5xqFjh56AwM",
          "duration": "58m",
          "difficulty": "Medium",
          "why": "Python-developer friendly MCP perspective.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 9,
        "part": 2,
        "partName": "PART 2: Orchestration, Agents & Workflows",
        "title": "LangGraph And Model Context Protocol (MCP)",
        "time": "14 Hours",
        "cumTime": "101h (45.9%)",
        "techs": [
          "LangGraph",
          "Model Context Protocol (MCP)",
          "Cyclic Graphs",
          "State Checkpointing"
        ],
        "goal": "Transition from linear DAG chains to cyclic, stateful graph architectures using LangGraph. Master Anthropic's Model Context Protocol (MCP) to build standardized client-server integrations for IDEs and enterprise agents.",
        "whyMatters": "Linear chains break when an LLM needs to loop back and retry a failed database query. LangGraph enables deterministic cyclic loops with human-in-the-loop approval. MCP is the emerging USB-C standard connecting AI models to internal enterprise tools.",
        "javaAnalogy": "LangGraph is Spring State Machine / BPMN (Camunda) for LLMs. MCP is like a standardized JDBC or gRPC protocol interface allowing any AI client to talk to any internal server.",
        "videos": [
          {
            "step": "9.1",
            "title": "How to Build AI Agents with LangGraph (Complete Guide)",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=R8K849j2w1E",
            "duration": "1h 05m",
            "difficulty": "Advanced",
            "whyBest": "The definitive LangGraph tutorial. Demonstrates StateGraph initialization, conditional edges, reducer functions, PostgreSQL checkpointing, and human-in-the-loop interrupt nodes.",
            "skip": "Skip basic LangChain recap; jump straight to StateGraph syntax."
          },
          {
            "step": "9.2",
            "title": "Build and Ship Any MCP Server in MINUTES (Full Guide)",
            "creator": "Cole Medin",
            "url": "https://www.youtube.com/watch?v=Zw3sfAIpeH8",
            "duration": "31m",
            "difficulty": "Intermediate",
            "whyBest": "Hands-on MCP server build and deployment workflow from a creator focused on practical AI engineering.",
            "skip": "Skip setup recap if your environment is ready.",
            "id": "Zw3sfAIpeH8"
          },
          {
            "step": "9.3",
            "title": "LangGraph Crash Course with code examples",
            "creator": "Sam Witteveen",
            "url": "https://www.youtube.com/watch?v=PqS1kib7RTw",
            "duration": "39m 1s",
            "difficulty": "Advanced",
            "whyBest": "Code-first LangGraph walkthrough that builds the graph mental model needed for reliable agent workflows.",
            "skip": "None.",
            "id": "PqS1kib7RTw"
          }
        ],
        "miniProject": "FastMCP Custom Server: Build a Python MCP server that exposes two enterprise tools: `check_server_uptime(host)` and `fetch_log_tail(service)`. Connect and test using Claude Desktop.",
        "prodProject": "Self-Healing CI/CD DevOps Agent with LangGraph & MCP: Build a stateful graph where an agent reads a failing git build log via an MCP server tool, generates a code fix, runs pytest in a docker sandbox, and loops up to 3 times before requesting human review.",
        "repos": [
          {
            "name": "langchain-ai/langgraph",
            "desc": "Library for building stateful, multi-actor applications with LLMs.",
            "url": "https://github.com/langchain-ai/langgraph"
          },
          {
            "name": "modelcontextprotocol/python-sdk",
            "desc": "Official Python SDK for the Model Context Protocol (MCP).",
            "url": "https://github.com/modelcontextprotocol/python-sdk"
          }
        ],
        "docs": [
          {
            "name": "LangGraph Official Tutorial",
            "url": "https://langchain-ai.github.io/langgraph/"
          },
          {
            "name": "Model Context Protocol Specification",
            "url": "https://modelcontextprotocol.io/introduction"
          }
        ],
        "mistakes": [
          "Forgetting to pass reducers (like operator.add) to StateGraph state lists, causing messages to overwrite instead of append.",
          "Building linear chains when the business logic requires cyclic error-recovery loops."
        ],
        "interviewQA": [
          {
            "q": "Why is LangGraph architecturally superior to standard LangChain for enterprise agents?",
            "a": "LangChain chains are linear Directed Acyclic Graphs (DAGs) which cannot loop back when a tool call fails. LangGraph models workflows as cyclic state machines with persistence checkpointers (PostgreSQL/SQLite), allowing deterministic retry loops, human-in-the-loop pauses, and time-travel debugging across execution steps."
          },
          {
            "q": "What problem does the Model Context Protocol (MCP) solve in enterprise AI?",
            "a": "Previously, every AI client (Cursor, Claude Desktop, custom chatbot) had to write custom M*N integrations for internal tools (Jira, Slack, SQL). MCP establishes a standardized client-server protocol over JSON-RPC/SSE, allowing one MCP server to expose tools and resources universally to any compatible AI client."
          }
        ],
        "checklist": [
          "Built a cyclic LangGraph StateGraph with conditional edges and SQLite/Postgres persistence.",
          "Implemented human-in-the-loop interrupt nodes that pause execution until admin approval.",
          "Deployed a functioning Python MCP Server that connects seamlessly to external LLM clients."
        ]
      },
      "projectVideos": [
        {
          "title": "Agentic AI With Langgraph And MCP Crash Course-Part 1",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=dIb-DujRNEo",
          "duration": "2h 27m",
          "why": "Direct LangGraph plus MCP implementation path for modern agent tooling.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "How to build MCP Client using LangGraph | Agentic AI using LangGraph",
          "creator": "CampusX",
          "url": "https://www.youtube.com/watch?v=yZGjVA4uDc4",
          "duration": "45m",
          "why": "Focused MCP client project using LangGraph.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Agentic AI Project in LangGraph | Build Lovable Clone",
          "creator": "codebasics",
          "url": "https://www.youtube.com/watch?v=SP-b_G74Nuk",
          "duration": "1h 8m",
          "why": "Real project-style LangGraph build that feels closer to product engineering.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 13,
      "name": "AI Agents, CrewAI, AG2/AutoGen And n8n Workflows",
      "time": "Time: 12-14h. Cumulative: 150h.",
      "videos": [
        {
          "order": 1,
          "title": "Building AI Agents in Pure Python - Beginner Course",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=bZzyPscbtI8",
          "id": "bZzyPscbtI8",
          "duration": "47m",
          "difficulty": "Medium",
          "why": "Shows agent mechanics without framework magic.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "CrewAI Tutorial - Agentic AI Tutorial",
          "creator": "codebasics",
          "url": "https://www.youtube.com/watch?v=G42J2MSKyc8",
          "id": "G42J2MSKyc8",
          "duration": "1h 11m",
          "difficulty": "Medium",
          "why": "Best practical CrewAI intro from metadata pass.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "AutoGen Crash Course For Beginners",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=R8KQ5nwpXl8",
          "id": "R8KQ5nwpXl8",
          "duration": "48m",
          "difficulty": "Medium",
          "why": "Better-known practical educator for AutoGen/AG2-style multi-agent patterns than random short demos.",
          "skip": "Watch for concepts; use LangGraph for most production stateful agents."
        },
        {
          "order": 4,
          "title": "n8n Quick Start Tutorial: Build Your First AI Agent [2026]",
          "creator": "n8n and Flowgrammer",
          "url": "https://www.youtube.com/watch?v=GuaKeDS6UKU",
          "id": "GuaKeDS6UKU",
          "duration": "21m",
          "difficulty": "Easy",
          "why": "Current n8n AI workflow starter.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 10,
        "part": 2,
        "partName": "PART 2: Orchestration, Agents & Workflows",
        "title": "AI Agents, CrewAI, AG2/AutoGen And n8n Workflows",
        "time": "14 Hours",
        "cumTime": "115h (52.3%)",
        "techs": [
          "AI Agents",
          "CrewAI",
          "AG2 / AutoGen",
          "Role-Based Delegation",
          "Memory Sets"
        ],
        "goal": "Build autonomous multi-agent systems where specialized AI personas (e.g., Architect, Senior Coder, QA Reviewer) collaborate asynchronously over shared memory and toolsets to accomplish complex software engineering objectives.",
        "whyMatters": "A single prompt cannot write, review, and test a 2,000-line microservice. Multi-agent systems divide labor among specialized personas with distinct instructions, reducing hallucination and solving complex enterprise problems.",
        "javaAnalogy": "Multi-Agent systems are like a microservices architecture where services communicate asynchronously via event buses, each having specialized responsibilities and domain boundaries.",
        "videos": [
          {
            "step": "10.1",
            "title": "CrewAI Tutorial: Complete Crash Course for Beginners",
            "creator": "aiwithbrandon",
            "url": "https://www.youtube.com/watch?v=sPzc6hMg7So",
            "duration": "1h 5m 43s",
            "difficulty": "Intermediate",
            "whyBest": "Full practical CrewAI course with enough depth to understand roles, tasks, tools, and collaboration patterns.",
            "skip": "Skip basic agent definitions if you completed the prior agent module.",
            "id": "sPzc6hMg7So"
          },
          {
            "step": "10.2",
            "title": "Agentic AI With Autogen Crash Course",
            "creator": "Krish Naik",
            "url": "https://www.youtube.com/watch?v=yDpV_jgO93c",
            "duration": "4h 4m 6s",
            "difficulty": "Advanced",
            "whyBest": "Long-form practical AutoGen/AG2-style agent implementation reference; keep as optional because LangGraph is the primary enterprise path.",
            "skip": "Watch selectively for AG2/AutoGen patterns; do not repeat generic agent intros.",
            "id": "yDpV_jgO93c"
          },
          {
            "step": "10.3",
            "title": "How to Build an Agent with the OpenAI Agents SDK",
            "creator": "Sam Witteveen",
            "url": "https://www.youtube.com/watch?v=0Z7u6DTDZ8o",
            "duration": "15m 59s",
            "difficulty": "Intermediate",
            "whyBest": "Modern coding-oriented agent SDK walkthrough that maps well to assistant and automation products.",
            "skip": "None.",
            "id": "0Z7u6DTDZ8o"
          }
        ],
        "miniProject": "Automated Tech Radar Research Crew: Use CrewAI to build a 3-agent crew (Researcher, Analyst, Technical Writer) that scrapes top AI blogs and compiles a weekly executive Markdown report.",
        "prodProject": "Enterprise Automated Pull Request Reviewer & Refactoring Suite: Build a multi-agent system that triggers on GitHub PR webhooks, diffs code changes, checks security rules, runs unit tests in Docker, and posts inline automated review comments.",
        "repos": [
          {
            "name": "joaomdmoura/crewAI",
            "desc": "Leading framework for orchestrating autonomous AI agent crews.",
            "url": "https://github.com/joaomdmoura/crewAI"
          },
          {
            "name": "ag2ai/ag2",
            "desc": "Microsoft's evolutionary continuation of AutoGen for multi-agent conversations.",
            "url": "https://github.com/ag2ai/ag2"
          }
        ],
        "docs": [
          {
            "name": "CrewAI Core Concepts Docs",
            "url": "https://docs.crewai.com/core-concepts/Crews/"
          },
          {
            "name": "AG2 / AutoGen User Guide",
            "url": "https://docs.ag2.ai/docs/getting-started"
          }
        ],
        "mistakes": [
          "Giving multiple agents identical tools and overlapping personas, causing them to argue or repeat work.",
          "Running multi-agent loops without hard step limits (max_iterations), resulting in infinite token consumption."
        ],
        "interviewQA": [
          {
            "q": "What is the difference between Sequential, Hierarchical, and Graph-based multi-agent collaboration?",
            "a": "Sequential executes tasks like an assembly line (Agent A -> Agent B -> Agent C). Hierarchical introduces a Manager agent that dynamically evaluates progress and assigns tasks to workers based on capabilities. Graph-based (LangGraph) defines explicit state transitions and conditional branching rules, offering the highest deterministic control for enterprise software."
          },
          {
            "q": "How do you mitigate cumulative hallucination across multi-agent pipelines?",
            "a": "Implement a dedicated 'QA / Auditor Agent' whose sole persona and prompt instruction is to verify worker outputs against retrieved ground truth facts and schema validators, rejecting unverified claims back to the worker before advancing the state."
          }
        ],
        "checklist": [
          "Configured multi-agent crews with clear role boundaries, backstories, and shared tools.",
          "Implemented hierarchical delegation where a Manager agent assigns tasks dynamically.",
          "Benchmarked token consumption and latency across CrewAI and LangGraph architectures."
        ]
      },
      "projectVideos": [
        {
          "title": "crewAI Crash Course For Beginners - Create Multi AI Agent For Complex Usecases",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=UV81LAb3x2g",
          "duration": "33m",
          "why": "Practical CrewAI multi-agent project with clear roles and workflow structure.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Build an AI Agent From Scratch in Python - Tutorial for Beginners",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=bTMPwUgLZf0",
          "duration": "34m",
          "why": "Framework-light agent project that clarifies the core loop.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ],
      "supplementalTracks": [
        {
          "id": 11,
          "part": 2,
          "partName": "PART 2: Orchestration, Agents & Workflows",
          "title": "Enterprise No-Code/Low-Code AI Workflows (n8n)",
          "time": "8 Hours",
          "cumTime": "123h (55.9%)",
          "techs": [
            "n8n AI Workflows",
            "Webhook Triggers",
            "CRM Integrations",
            "Low-Code Pipelines"
          ],
          "goal": "Master self-hosted n8n to architect visual, production-grade AI automated workflows that connect LLMs, vector databases, and custom Python microservices directly into Slack, Jira, Salesforce, and PostgreSQL.",
          "whyMatters": "You should not write 500 lines of Python boilerplate just to connect a Slack webhook to an LLM and update a Jira ticket. Self-hosted n8n provides visual workflow orchestration with native AI node support, reducing automation delivery time by 80%.",
          "javaAnalogy": "n8n is Apache Camel or Spring Integration for the modern AI era—visual Enterprise Application Integration (EAI) routing with built-in webhook connectors.",
          "videos": [
            {
              "step": "10A.2",
              "title": "n8n AI Agent Tutorial for Beginners",
              "creator": "n8n Official",
              "url": "https://www.youtube.com/watch?v=PEI_ePNNfJQ",
              "duration": "31m",
              "difficulty": "Medium",
              "whyBest": "Official n8n-style AI workflow training for agent nodes, tools, and automation patterns.",
              "skip": "Skip UI basics if comfortable."
            }
          ],
          "miniProject": "Slack AI DevOps Concierge: Build an n8n workflow triggered by a Slack Slash command `/devops` that queries AWS status APIs, summarizes alerts via a current Claude Haiku model, and replies in thread.",
          "prodProject": "Enterprise Automated Lead Qualification & CRM Ingestion Pipeline: Build a self-hosted n8n workflow that captures webhook form submissions, enriches company data via Clearbit API, scores leads using GPT-4o structured outputs, and inserts rows into PostgreSQL.",
          "repos": [
            {
              "name": "n8n-io/n8n",
              "desc": "Fair-code licensed workflow automation tool with native AI nodes.",
              "url": "https://github.com/n8n-io/n8n"
            },
            {
              "name": "n8n-io/self-hosted-ai-starter-kit",
              "desc": "Docker Compose starter kit for self-hosting n8n + Qdrant + Postgres + Ollama.",
              "url": "https://github.com/n8n-io/self-hosted-ai-starter-kit"
            }
          ],
          "docs": [
            {
              "name": "n8n Advanced AI Node Documentation",
              "url": "https://docs.n8n.io/advanced-ai/"
            },
            {
              "name": "Self-Hosting n8n in Production Guide",
              "url": "https://docs.n8n.io/hosting/"
            }
          ],
          "mistakes": [
            "Exposing self-hosted n8n webhook endpoints to public traffic without Basic Auth or header API key validation.",
            "Executing heavy Python data transformation loops inside n8n code nodes instead of calling external FastAPI microservices."
          ],
          "interviewQA": [
            {
              "q": "When should an enterprise choose low-code n8n AI workflows over custom LangGraph Python microservices?",
              "a": "Choose n8n when the workflow is primarily an event-driven integration between third-party SaaS APIs (Slack, Salesforce, Jira, HubSpot) where building and maintaining 50 OAuth/API connectors in Python is uneconomical. Choose LangGraph when the application requires complex custom domain state machines, heavy algorithmic data processing, or sub-second UI streaming."
            },
            {
              "q": "How do you secure secrets and credentials in self-hosted n8n workflows?",
              "a": "Never hardcode API keys in node configurations. Store credentials inside n8n's encrypted Credential Store (backed by a secure encryption key in environment variables) or integrate n8n with HashiCorp Vault / AWS Secrets Manager via custom environment variables."
            }
          ],
          "checklist": [
            "Deployed self-hosted n8n via Docker Compose with secure PostgreSQL backend.",
            "Constructed visual AI Agent nodes with sub-workflow tool calling and memory buffers.",
            "Integrated n8n webhooks seamlessly with external custom Python FastAPI services."
          ]
        }
      ]
    },
    {
      "num": 14,
      "name": "Background Workers And Evaluation",
      "time": "Time: 8-10h. Cumulative: 162h.",
      "videos": [
        {
          "order": 1,
          "title": "Background Tasks with FastAPI Background Tasks and Celery + Redis",
          "creator": "Ssali Jonathan",
          "url": "https://www.youtube.com/watch?v=eAHAKowv6hk",
          "id": "eAHAKowv6hk",
          "duration": "45m",
          "difficulty": "Medium",
          "why": "Direct FastAPI worker implementation.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Getting Started With Celery: Asynchronous Tasks in Python",
          "creator": "Pretty Printed",
          "url": "https://www.youtube.com/watch?v=VRHVEporra0",
          "id": "VRHVEporra0",
          "duration": "12m",
          "difficulty": "Easy",
          "why": "Quick Celery mental model.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "How to Systematically Setup LLM Evals",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=a3SMraZWNNs",
          "id": "a3SMraZWNNs",
          "duration": "55m",
          "difficulty": "Advanced",
          "why": "Best practical eval design video in this list.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "The 100% EASIEST Way to Test LLMs & AI Agents",
          "creator": "Execute Automation",
          "url": "https://www.youtube.com/watch?v=uz5BEadZwLc",
          "id": "uz5BEadZwLc",
          "duration": "19m",
          "difficulty": "Medium",
          "why": "Reinforces practical agent/LLM testing.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "DeepEval for RAG: Let's Test If Your LLM Really Works",
          "creator": "Execute Automation",
          "url": "https://www.youtube.com/watch?v=3g5CbfXsm_8",
          "id": "3g5CbfXsm_8",
          "duration": "20m",
          "difficulty": "Medium",
          "why": "Practical DeepEval RAG tests.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 17,
        "part": 3,
        "partName": "PART 3: Backend Infrastructure & Data Tier",
        "title": "Background Workers And Evaluation",
        "time": "7 Hours",
        "cumTime": "180h (81.8%)",
        "techs": [
          "Evaluation (Ragas/DeepEval)",
          "Guardrails (NeMo)",
          "LLM-as-a-Judge",
          "Hallucination Detection"
        ],
        "goal": "Implement quantitative CI/CD evaluation suites using Ragas and DeepEval to measure RAG Faithfulness, Answer Relevance, and Context Recall. Deploy real-time safety interceptors using NVIDIA NeMo Guardrails and Llama Guard to block prompt injections and PII leakage.",
        "whyMatters": "You cannot deploy an AI system to production based on *'it looks good to me on 3 test prompts'*. Enterprise engineering requires automated unit tests for AI outputs, ensuring model updates do not introduce regressions or toxic behaviors.",
        "javaAnalogy": "Ragas/DeepEval are your JUnit 5, Mockito, and SonarQube quality gates for AI. NeMo Guardrails is Spring Security filter chains intercepting malicious web payloads.",
        "videos": [],
        "miniProject": "DeepEval CI/CD Unit Test Suite: Write a Python test suite using `deepeval` that runs 10 test questions against your RAG pipeline, asserting that `FaithfulnessMetric >= 0.85`, failing the pytest run if hallucinations occur.",
        "prodProject": "Enterprise Zero-Trust AI Security Gateway with NeMo Guardrails: Build a production API gateway wrapping OpenAI GPT-4o that executes a 3-stage validation pipeline: (1) Inbound guardrail screening prompt for injection/jailbreaks; (2) LLM execution; (3) Outbound guardrail verifying zero PII leakage and zero competitor brand mentions before returning to client.",
        "repos": [
          {
            "name": "explodinggradients/ragas",
            "desc": "Evaluation framework for Retrieval Augmented Generation (RAG) pipelines.",
            "url": "https://github.com/explodinggradients/ragas"
          },
          {
            "name": "NVIDIA/NeMo-Guardrails",
            "desc": "Open-source toolkit for easily adding programmable guardrails to LLM systems.",
            "url": "https://github.com/NVIDIA/NeMo-Guardrails"
          }
        ],
        "docs": [
          {
            "name": "Ragas Metric Definitions & Math",
            "url": "https://docs.ragas.io/en/stable/concepts/metrics/index.html"
          },
          {
            "name": "NVIDIA NeMo Guardrails Documentation",
            "url": "https://docs.nvidia.com/nemo/guardrails/index.html"
          }
        ],
        "mistakes": [
          "Relying solely on subjective human evaluation instead of running automated nightly CI/CD regression tests.",
          "Applying heavy LLM-based guardrails on every single chat turn, causing unacceptable 3-second latency penalties."
        ],
        "interviewQA": [
          {
            "q": "How does the Ragas 'Faithfulness' metric detect LLM hallucinations mathematically?",
            "a": "Faithfulness uses an LLM-as-a-Judge to break the generated answer down into discrete factual statements. It then cross-references each statement against the retrieved RAG context chunks. Faithfulness is calculated as (Number of Verified Statements) / (Total Statements Generated). A score < 1.0 indicates hallucination."
          },
          {
            "q": "What is the difference between input guardrails and output guardrails in NeMo Guardrails?",
            "a": "Input guardrails execute before the prompt reaches the LLM, intercepting jailbreaks, prompt injections, or off-topic queries. Output guardrails execute after the LLM generates a response, inspecting the text to redact leaked PII, block competitor brand names, or suppress toxic content before delivery to the client."
          }
        ],
        "checklist": [
          "Authored automated unit tests for RAG accuracy using DeepEval/Ragas with clear assertion thresholds.",
          "Configured NVIDIA NeMo Guardrails with custom Colang syntax rules to block prompt injections.",
          "Integrated LLM-as-a-Judge evaluation scripts directly into automated CI/CD deployment pipelines."
        ]
      },
      "projectVideos": [
        {
          "title": "RAGAS: How to Evaluate a RAG Application Like a Pro for Beginners",
          "creator": "Mervin Praison",
          "url": "https://www.youtube.com/watch?v=5fp6e5nhJRk",
          "duration": "9m",
          "why": "Compact RAGAS project for retrieval/answer quality metrics.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "RAG Evaluation Using DeepEval & Confident AI - Full Tutorial",
          "creator": "Yash Patil",
          "url": "https://www.youtube.com/watch?v=402EyLS59ho",
          "duration": "29m",
          "why": "More complete DeepEval project for RAG test suites.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 15,
      "name": "LLMOps Observability: LangSmith, Langfuse, OpenTelemetry And APM",
      "time": "Time: 8-10h. Cumulative: 172h.",
      "videos": [
        {
          "order": 1,
          "title": "What Is LangSmith? Explained in 5 Minutes",
          "creator": "LangChain",
          "url": "https://www.youtube.com/watch?v=kYtnLaJeia8",
          "id": "kYtnLaJeia8",
          "duration": "5m",
          "difficulty": "Easy",
          "why": "Official quick mental model.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "LangSmith Tutorial - LLM Evaluation for Beginners",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=tFXm5ijih98",
          "id": "tFXm5ijih98",
          "duration": "36m",
          "difficulty": "Medium",
          "why": "Hands-on LangSmith eval workflow.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "10 min Walkthrough of Langfuse",
          "creator": "Langfuse",
          "url": "https://www.youtube.com/watch?v=2E8iTvGo9Hs",
          "id": "2E8iTvGo9Hs",
          "duration": "10m",
          "difficulty": "Easy",
          "why": "Official Langfuse orientation.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Get Started with Langfuse - Open-Source LLM Monitoring",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=epnPfe5am3I",
          "id": "epnPfe5am3I",
          "duration": "12m",
          "difficulty": "Medium",
          "why": "Practical open-source tracing setup.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "Intro to OpenTelemetry and LLM Observability",
          "creator": "Arize AI",
          "url": "https://www.youtube.com/watch?v=0I0ZrmyoTpM",
          "id": "0I0ZrmyoTpM",
          "duration": "16m",
          "difficulty": "Medium",
          "why": "Connects AI traces to broader observability.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 12,
        "part": 2,
        "partName": "PART 2: Orchestration, Agents & Workflows",
        "title": "LLMOps Observability: LangSmith, Langfuse, OpenTelemetry And APM",
        "time": "7 Hours",
        "cumTime": "130h (59.1%)",
        "techs": [
          "LangSmith",
          "Langfuse",
          "OpenTelemetry",
          "Grafana",
          "Splunk",
          "Dynatrace",
          "Token Cost Attribution"
        ],
        "goal": "Implement full-stack distributed tracing and observability across multi-step LLM chains and agent workflows using LangSmith, Langfuse, OpenTelemetry, and enterprise APM destinations like Grafana, Splunk, and Dynatrace.",
        "whyMatters": "FDE and enterprise AI teams often land in companies that already standardize on Grafana, Splunk, or Dynatrace. You need LLM-native traces for prompts/tools/costs, plus OpenTelemetry export so AI systems fit existing SRE, incident, and compliance workflows.",
        "javaAnalogy": "LangSmith and Langfuse are LLM-native tracing layers. OpenTelemetry is the Micrometer/Sleuth-style instrumentation bridge. Grafana, Splunk, and Dynatrace are the enterprise observability platforms where SRE teams monitor production systems.",
        "videos": [],
        "miniProject": "Langfuse Tracing Integration: Wrap Module 3's SQL Database Assistant with Langfuse decorators. Execute 5 multi-tool queries and verify latency tree and token cost rendering in dashboard.",
        "prodProject": "Multi-Tenant Enterprise Observability & Cost Alerting Pipeline: Deploy self-hosted Langfuse via Docker, instrument a FastAPI AI backend with OpenTelemetry correlation IDs, and design exports/dashboards for Grafana, Splunk, or Dynatrace so tenant spend, latency, errors, and traces are visible to enterprise SRE teams.",
        "repos": [
          {
            "name": "langfuse/langfuse",
            "desc": "Open-source LLM engineering platform: tracing, evaluations, prompt management.",
            "url": "https://github.com/langfuse/langfuse"
          },
          {
            "name": "langchain-ai/langsmith-sdk",
            "desc": "Official Python SDK for LangSmith distributed tracing.",
            "url": "https://github.com/langchain-ai/langsmith-sdk"
          }
        ],
        "docs": [
          {
            "name": "Langfuse Quickstart Guide",
            "url": "https://langfuse.com/docs/get-started"
          },
          {
            "name": "LangSmith Tracing Concepts",
            "url": "https://docs.smith.langchain.com/tracing"
          },
          {
            "name": "Grafana OpenTelemetry Docs",
            "url": "https://grafana.com/docs/grafana-cloud/monitor-applications/application-observability/instrument/opentelemetry/"
          },
          {
            "name": "Splunk OpenTelemetry Collector Docs",
            "url": "https://docs.splunk.com/Observability/gdi/opentelemetry/opentelemetry.html"
          },
          {
            "name": "Dynatrace OpenTelemetry Docs",
            "url": "https://docs.dynatrace.com/docs/ingest-from/opentelemetry"
          }
        ],
        "mistakes": [
          "Failing to inject correlation IDs and user_id metadata into root trace spans, making incident post-mortems impossible.",
          "Logging sensitive customer PII or API passwords in unencrypted plain text inside trace payload sinks.",
          "Treating LangSmith/Langfuse as a replacement for enterprise observability instead of exporting correlation IDs, traces, logs, and metrics into the customer standard platform such as Grafana, Splunk, or Dynatrace."
        ],
        "interviewQA": [
          {
            "q": "Why is distributed tracing more critical for LLM applications than traditional web REST services?",
            "a": "Traditional REST services execute deterministic database queries and return in <50ms. LLM agent workflows execute probabilistic loops, invoking external tools, vector searches, and 5+ sequential LLM completions over 15-30 seconds. Tracing is required to isolate which exact prompt step caused a hallucination, latency spike, or cost blowout."
          },
          {
            "q": "How does Langfuse calculate dollar cost attribution across multi-tenant enterprise organizations?",
            "a": "Langfuse intercepts API completion metadata, extracting model name and exact prompt/completion token counts. It maps these against a dynamic model pricing table (e.g. $2.50/1M input tokens for current flagship GPT model) and aggregates spend by grouping on custom metadata tags (tenant_id, user_id, environment)."
          }
        ],
        "checklist": [
          "Configured distributed tracing across multi-step Python agent workflows using `@traceable`.",
          "Deployed self-hosted Langfuse via Docker with PostgreSQL storage.",
          "Verified token cost attribution and latency breakdown per tool execution span.",
          "Explained when to use LangSmith/Langfuse versus Grafana, Splunk, or Dynatrace in an enterprise incident workflow.",
          "Mapped OpenTelemetry traces, logs, and metrics from an AI backend into an enterprise APM destination."
        ]
      },
      "projectVideos": [
        {
          "title": "LangSmith Crash Course | Observability in GenAI",
          "creator": "CampusX",
          "url": "https://www.youtube.com/watch?v=4FFspU4riHk",
          "duration": "2h 8m",
          "why": "Hands-on LangSmith project-style walkthrough for traces and debugging.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "OpenTelemetry: How distributed tracing really works with Python, FastAPI and requests",
          "creator": "Adam Gardner",
          "url": "https://www.youtube.com/watch?v=azyVG0T1aVc",
          "duration": "9m",
          "why": "Useful SRE bridge from AI observability into normal distributed tracing.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "OpenTelemetry FastAPI Tutorial: Get GREAT App Performance NOW!",
          "creator": "Eric Roby",
          "url": "https://www.youtube.com/watch?v=m28TTogdcbk",
          "duration": "15m",
          "why": "FastAPI/OpenTelemetry instrumentation project relevant to enterprise APM handoff.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 16,
      "name": "Guardrails And Production Safety",
      "time": "Time: 8-10h. Cumulative: 184h.",
      "videos": [
        {
          "order": 1,
          "title": "NVIDIA NeMo Guardrails: Full Walkthrough for Chatbots / AI",
          "creator": "James Briggs",
          "url": "https://www.youtube.com/watch?v=SwqusllMCnE",
          "id": "SwqusllMCnE",
          "duration": "21m",
          "difficulty": "Medium",
          "why": "Practical guardrails implementation.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Guardrails for LLM Applications",
          "creator": "Sunny Savita",
          "url": "https://www.youtube.com/watch?v=7V1w5gnZ-kw",
          "id": "7V1w5gnZ-kw",
          "duration": "1h 26m",
          "difficulty": "Medium",
          "why": "Full Guardrails AI walkthrough.",
          "skip": "Skip repeated basics."
        },
        {
          "order": 3,
          "title": "LLM Observability with OpenTelemetry - Ultimate Guide",
          "creator": "Agenta AI",
          "url": "https://www.youtube.com/watch?v=crEyMDJ4Bp0",
          "id": "crEyMDJ4Bp0",
          "duration": "9m",
          "difficulty": "Medium",
          "why": "OTel-focused LLM observability.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Building Production-Ready RAG Systems",
          "creator": "Conf42",
          "url": "https://www.youtube.com/watch?v=ASBY-UrPFv8",
          "id": "ASBY-UrPFv8",
          "duration": "19m",
          "difficulty": "Medium",
          "why": "Production architecture concerns.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "Building Production RAG Systems: Architecture, Scaling & Cost Optimization",
          "creator": "Mukul Raina",
          "url": "https://www.youtube.com/watch?v=uZ56v9xfcBw",
          "id": "uZ56v9xfcBw",
          "duration": "1h 5m",
          "difficulty": "Advanced",
          "why": "Architecture, scaling, cost in one video.",
          "skip": "None."
        }
      ],
      "desktop": {
        "id": 18,
        "part": 4,
        "partName": "PART 4: Production Architecture & Portfolio Systems",
        "title": "Guardrails And Production Safety",
        "time": "12 Hours",
        "cumTime": "192h (87.3%)",
        "techs": [
          "Production Ops",
          "Scaling AI Systems",
          "Cost Optimization",
          "LLM Security (OWASP Top 10)",
          "KV-Cache"
        ],
        "goal": "Master the operational discipline of running AI at enterprise scale. Optimize token costs via semantic caching and model routing, scale inference throughput using KV-cache awareness, and defend against OWASP Top 10 LLM vulnerabilities.",
        "whyMatters": "Unmonitored AI applications can bankrupt an enterprise via token loops ($10,000+ overnight bills) or expose critical databases via Indirect Prompt Injection. Production Ops separates amateur wrappers from enterprise architects.",
        "javaAnalogy": "This is site reliability engineering (SRE) and JVM garbage collection tuning applied to GPU token inference and API gateways.",
        "videos": [
          {
            "step": "18.1",
            "title": "How We Cut LLM Latency 70% With TensorRT in Production",
            "creator": "MLOps.community",
            "url": "https://www.youtube.com/watch?v=wTrv1hMQbVg",
            "duration": "1h 5m 20s",
            "difficulty": "Advanced",
            "whyBest": "Real production latency and inference optimization case study; useful even if you mostly consume hosted APIs.",
            "skip": "Skip low-level GPU sections if your target role is API-first FDE.",
            "id": "wTrv1hMQbVg"
          },
          {
            "step": "18.3",
            "title": "What is Prompt Caching? Optimize LLM Latency with AI Transformers",
            "creator": "IBM Technology",
            "url": "https://www.youtube.com/watch?v=u57EnkQaUTY",
            "duration": "9m 6s",
            "difficulty": "Intermediate",
            "whyBest": "Current practical cost/latency lever that applies across enterprise LLM systems.",
            "skip": "None.",
            "id": "u57EnkQaUTY"
          }
        ],
        "miniProject": "LLM Token Flood DoS Protector: Build a FastAPI middleware that tracks token generation rates per user IP using Redis sliding window rate limits, blocking users who request >50,000 tokens within 60 seconds.",
        "prodProject": "Enterprise Intelligent Cost-Aware Model Router & Budgeting Sink: Build a production routing proxy that analyzes incoming prompts, estimates task complexity using a classifier, routes simple classification tasks to low-cost models and complex coding tasks to stronger Claude/GPT models, and enforces hard monthly dollar budgets per tenant ID.",
        "repos": [
          {
            "name": "OWASP/www-project-top-10-for-large-language-model-applications",
            "desc": "Official OWASP security guidance for LLM applications.",
            "url": "https://github.com/OWASP/www-project-top-10-for-large-language-model-applications"
          },
          {
            "name": "vllm-project/vllm",
            "desc": "High-throughput and memory-efficient LLM serving engine with PagedAttention.",
            "url": "https://github.com/vllm-project/vllm"
          }
        ],
        "docs": [
          {
            "name": "OWASP Top 10 for LLMs Official Site",
            "url": "https://owasp.org/www-project-top-10-for-large-language-model-applications/"
          },
          {
            "name": "vLLM PagedAttention Architecture",
            "url": "https://docs.vllm.ai/en/latest/models/engine_args.html"
          }
        ],
        "mistakes": [
          "Allowing unbounded context window lengths in production without enforcing token pruning or maximum input lengths.",
          "Trusting LLM outputs directly in SQL execution engines without AST syntax checking and privilege isolation."
        ],
        "interviewQA": [
          {
            "q": "What is KV-Cache in LLM inference, and why does PagedAttention (vLLM) improve GPU serving throughput?",
            "a": "During autoregressive token generation, the GPU stores computed Key and Value vectors for all prior tokens in high-speed HBM memory (KV-Cache). Traditional serving pre-allocates contiguous memory blocks, leading to 60-80% memory fragmentation waste. PagedAttention divides KV-cache into non-contiguous virtual pages (like OS virtual memory), increasing GPU batching concurrency by 3x."
          },
          {
            "q": "How do you defend against Indirect Prompt Injection in enterprise RAG pipelines?",
            "a": "Indirect prompt injection occurs when an LLM reads external untrusted documents (emails, websites) containing hidden adversarial commands. Defense requires privilege separation: use a low-privileged parser LLM to extract structured summaries from documents first, stripping imperative command syntax before passing the clean data to the high-privileged reasoning agent."
          }
        ],
        "checklist": [
          "Implemented cascading model routing architectures that slash token costs by up to 70%.",
          "Configured rate-limiting and token budgeting middleware to prevent Denial of Service (DoS).",
          "Audited AI codebases against the OWASP Top 10 for LLM Applications, closing injection vulnerabilities."
        ]
      },
      "projectVideos": [
        {
          "title": "Learn to Build Production-Ready LLM Guardrails From Scratch!",
          "creator": "Pavan Belagatti",
          "url": "https://www.youtube.com/watch?v=zBV-xAWNEKk",
          "duration": "12m",
          "why": "Useful from-scratch guardrails project for understanding runtime safety checks.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Guardrails with LangChain: A Complete Crash Course for Building Safe AI Agents",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=ruiLq0OzjkI",
          "duration": "38m",
          "why": "Practical guardrails implementation for agent workflows.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Ingest OpenTelemetry Traces to Dynatrace without the OneAgent",
          "creator": "Dynatrace",
          "url": "https://www.youtube.com/watch?v=YlUYASPEqn8",
          "duration": "5m",
          "why": "Short official Dynatrace/OpenTelemetry handoff pattern for enterprise APM.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 17,
      "name": "Docker, Kubernetes And CI/CD For AI Apps",
      "time": "Time: 10-12h. Cumulative: 196h.",
      "videos": [
        {
          "order": 1,
          "title": "Docker Crash Course for Absolute Beginners [NEW]",
          "creator": "TechWorld with Nana",
          "url": "https://www.youtube.com/watch?v=pg19Z8LL06w",
          "id": "pg19Z8LL06w",
          "duration": "1h 8m",
          "difficulty": "Easy",
          "why": "Modern Docker primer.",
          "skip": "Skip basics if comfortable."
        },
        {
          "order": 2,
          "title": "Docker Tutorial for Beginners [FULL COURSE in 3 Hours]",
          "creator": "TechWorld with Nana",
          "url": "https://www.youtube.com/watch?v=3c-iBn73dDE",
          "id": "3c-iBn73dDE",
          "duration": "2h 46m",
          "difficulty": "Medium",
          "why": "Complete Docker reference.",
          "skip": "Watch build/deploy chapters."
        },
        {
          "order": 3,
          "title": "What is Kubernetes? Kubernetes Explained in 15 mins",
          "creator": "TechWorld with Nana",
          "url": "https://www.youtube.com/watch?v=VnvRFRk_51k",
          "id": "VnvRFRk_51k",
          "duration": "14m",
          "difficulty": "Easy",
          "why": "Clear architecture intro from the best practical Kubernetes educator for application developers.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Kubernetes Crash Course for Absolute Beginners [NEW]",
          "creator": "TechWorld with Nana",
          "url": "https://www.youtube.com/watch?v=s_o8dwzRlu4",
          "id": "s_o8dwzRlu4",
          "duration": "1h 12m",
          "difficulty": "Medium",
          "why": "Practical K8s path.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "Kubernetes Tutorial for Beginners [FULL COURSE in 4 Hours]",
          "creator": "TechWorld with Nana",
          "url": "https://www.youtube.com/watch?v=X48VuDVv0do",
          "id": "X48VuDVv0do",
          "duration": "3h 37m",
          "difficulty": "Medium",
          "why": "Full reference.",
          "skip": "Use selectively."
        }
      ],
      "desktop": {
        "id": 13,
        "part": 3,
        "partName": "PART 3: Backend Infrastructure & Data Tier",
        "title": "Docker, Kubernetes And CI/CD For AI Apps",
        "time": "15 Hours",
        "cumTime": "145h (65.9%)",
        "techs": [
          "FastAPI",
          "Docker",
          "Kubernetes",
          "Async Web Servers",
          "GPU Scheduling"
        ],
        "goal": "Architect asynchronous, high-throughput Python REST APIs using FastAPI. Containerize AI microservices using multi-stage Docker builds, and understand Kubernetes deployment patterns for scheduling CPU/GPU AI workloads.",
        "whyMatters": "AI models cannot live in isolation; they must be served via robust HTTP/gRPC APIs that withstand concurrent traffic spikes, memory leaks, and connection pooling without crashing production servers.",
        "javaAnalogy": "FastAPI is your Spring Boot MVC / WebFlux framework. Pydantic dependency injection in FastAPI mirrors `@Autowired` / Spring IOC containers.",
        "videos": [],
        "miniProject": "FastAPI Streaming Service: Build a FastAPI service with an endpoint `/api/v1/chat/stream` that streams OpenAI token responses via `StreamingResponse(..., media_type='text/event-stream')`.",
        "prodProject": "Production Dockerized & K8s-Ready AI Gateway Microservice: Build a production FastAPI service with custom middleware for JWT authentication and request correlation IDs. Write a multi-stage Dockerfile and complete K8s manifests (`Deployment.yaml`, `Service.yaml`, `HPA.yaml`) configured with liveness probes and resource limits.",
        "repos": [
          {
            "name": "tiangolo/fastapi",
            "desc": "Modern, fast web framework for building APIs with Python 3.8+.",
            "url": "https://github.com/tiangolo/fastapi"
          },
          {
            "name": "astral-sh/uv-docker-example",
            "desc": "Official best practices for containerizing fast Python uv apps in Docker.",
            "url": "https://github.com/astral-sh/uv-docker-example"
          }
        ],
        "docs": [
          {
            "name": "FastAPI Dependency Injection Guide",
            "url": "https://fastapi.tiangolo.com/tutorial/dependencies/"
          },
          {
            "name": "Kubernetes Managing GPUs Docs",
            "url": "https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/"
          }
        ],
        "mistakes": [
          "Running single-process Uvicorn in production instead of using Gunicorn process managers with Uvicorn async workers (gunicorn -k uvicorn.workers.UvicornWorker).",
          "Failing to set memory requests and limits in Kubernetes, allowing memory-hungry embedding libraries to trigger Out-Of-Memory (OOMKilled) pod crashes."
        ],
        "interviewQA": [
          {
            "q": "How does FastAPI Dependency Injection (Depends) work under the hood compared to Spring Boot @Autowired?",
            "a": "Spring Boot uses reflection and a global ApplicationContext container initialized at startup to inject singletons. FastAPI evaluates functions passed to Depends() dynamically during request lifecycle routing, resolving sub-dependencies hierarchically and caching results per request scope."
          },
          {
            "q": "Why must you configure distinct Readiness and Liveness probes for AI pods in Kubernetes?",
            "a": "AI containers often take 10-30 seconds at startup to download models or initialize database pools. If Liveness probes fire too early, K8s will kill and restart the pod in an infinite loop. Readiness probes must check that models and DB pools are fully ready before routing ingress traffic, while Liveness checks basic event loop responsiveness."
          }
        ],
        "checklist": [
          "Built modular FastAPI applications using APIRouter and clean Dependency Injection.",
          "Optimized Docker container images under 400MB using multi-stage builds and virtual environments.",
          "Authored Kubernetes deployment manifests with CPU/Memory limits and readiness health checks."
        ]
      },
      "projectVideos": [
        {
          "title": "How to Deploy ML Solutions with FastAPI, Docker, & AWS",
          "creator": "Shaw Talebi",
          "url": "https://www.youtube.com/watch?v=pJ_nCklQ65w",
          "duration": "29m",
          "why": "Practical deployment path using the same FastAPI style AI services use.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Scaling ML Models with FastAPI, Docker, and Kubernetes: Practical Tutorial",
          "creator": "MLWorks",
          "url": "https://www.youtube.com/watch?v=6WMXI0izClk",
          "duration": "21m",
          "why": "Focused FastAPI + Docker + Kubernetes project for AI service scaling basics.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Create, Dockerize, and Deploy a Python App on Kubernetes",
          "creator": "Cloud&DevOpsCrafted",
          "url": "https://www.youtube.com/watch?v=cLbi2_6bXoE",
          "duration": "14m",
          "why": "Compact Kubernetes deployment walkthrough for Python services.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 18,
      "name": "Enterprise Cloud AI: AWS Bedrock, Azure AI And Vertex AI",
      "time": "Time: 10-12h. Cumulative: 208h.",
      "videos": [
        {
          "order": 1,
          "title": "Introducing Azure AI Foundry",
          "creator": "Microsoft Mechanics",
          "url": "https://www.youtube.com/watch?v=GD7MnIwAxYM",
          "id": "GD7MnIwAxYM",
          "duration": "13m",
          "difficulty": "Easy",
          "why": "Official Azure AI platform intro.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Azure AI Foundry Overview",
          "creator": "John Savill's Technical Training",
          "url": "https://www.youtube.com/watch?v=Sq8Cq7RZM2o",
          "id": "Sq8Cq7RZM2o",
          "duration": "1h 28m",
          "difficulty": "Medium",
          "why": "Deep, enterprise-oriented Azure explanation.",
          "skip": "Skip service catalog you do not use."
        },
        {
          "order": 3,
          "title": "Amazon Bedrock for Beginners - From First Prompt to AI Agent",
          "creator": "AWS Developers and Morgan Willis",
          "url": "https://www.youtube.com/watch?v=FAgmR9VV0GQ",
          "id": "FAgmR9VV0GQ",
          "duration": "45m",
          "difficulty": "Medium",
          "why": "Official/practical Bedrock path.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Introduction to Gemini on Vertex AI",
          "creator": "Google Cloud Tech",
          "url": "https://www.youtube.com/watch?v=YfiLUpNejpE",
          "id": "YfiLUpNejpE",
          "duration": "5m",
          "difficulty": "Easy",
          "why": "Official Vertex/Gemini intro.",
          "skip": "None."
        },
        {
          "order": 5,
          "title": "Run Google's Models on Vertex AI with Python + EU Data Residency Tips",
          "creator": "NeuralNine",
          "url": "https://www.youtube.com/watch?v=7HkCWwQhLWs",
          "id": "7HkCWwQhLWs",
          "duration": "10m",
          "difficulty": "Medium",
          "why": "Practical Python and compliance angle.",
          "skip": "Region details optional."
        }
      ],
      "desktop": {
        "id": 15,
        "part": 3,
        "partName": "PART 3: Backend Infrastructure & Data Tier",
        "title": "Enterprise Cloud AI: AWS Bedrock, Azure AI And Vertex AI",
        "time": "10 Hours",
        "cumTime": "165h (75.0%)",
        "techs": [
          "Azure OpenAI",
          "AWS Bedrock",
          "Google Vertex AI",
          "IAM Security",
          "VPC Endpoints"
        ],
        "goal": "Deploy and integrate cloud-managed AI platforms across the big three hyperscalers. Master enterprise IAM role authentication, private VPC endpoints, provisioned throughput, and data residency compliance (GDPR/HIPAA).",
        "whyMatters": "Fortune 500 enterprises rarely send API keys over public internet endpoints to OpenAI. They consume AI via private cloud agreements—Azure OpenAI Service, AWS Bedrock, or Google Vertex AI—ensuring zero data retention and strict network isolation.",
        "javaAnalogy": "Hyperscaler AI services are like consuming AWS RDS or Azure Service Bus via cloud IAM roles—managed infrastructure with enterprise SLA guarantees and private VNet integration.",
        "videos": [
          {
            "step": "15.3",
            "title": "Building AI agents on Google Cloud",
            "creator": "Google Cloud Tech",
            "url": "https://www.youtube.com/watch?v=8rlNdKywldQ",
            "duration": "26m 25s",
            "difficulty": "Intermediate",
            "whyBest": "Practical Google Cloud agent deployment coverage for Vertex-era enterprise AI work.",
            "skip": "None."
          }
        ],
        "miniProject": "AWS Bedrock Boto3 Wrapper: Build an async Python service using `aioboto3` that authenticates via AWS IAM credentials and calls a current Claude Sonnet model on Bedrock with streaming response assembly.",
        "prodProject": "Multi-Cloud Enterprise Failover AI Client: Build a unified Python foundation client that authenticates via Azure Managed Identity (Azure OpenAI), AWS IAM (Bedrock), and Google Application Default Credentials (Vertex AI), implementing automatic cross-cloud failover if a cloud region goes down.",
        "repos": [
          {
            "name": "Azure/azure-sdk-for-python",
            "desc": "Official Azure SDK for Python containing azure-ai-openai client.",
            "url": "https://github.com/Azure/azure-sdk-for-python"
          },
          {
            "name": "boto/boto3",
            "desc": "Official AWS SDK for Python (Boto3) for invoking Bedrock models.",
            "url": "https://github.com/boto/boto3"
          }
        ],
        "docs": [
          {
            "name": "Azure OpenAI Service Architecture Guide",
            "url": "https://learn.microsoft.com/en-us/azure/ai-services/openai/overview"
          },
          {
            "name": "AWS Bedrock API Reference",
            "url": "https://docs.aws.amazon.com/bedrock/latest/userguide/what-is-bedrock.html"
          }
        ],
        "mistakes": [
          "Using public internet API endpoints in enterprise cloud environments instead of configuring Private Link VPC Endpoints.",
          "Hardcoding cloud credentials instead of attaching AWS IAM Roles or Azure Managed Identities to execution pods."
        ],
        "interviewQA": [
          {
            "q": "Why do enterprise banks and healthcare organizations require Azure OpenAI or AWS Bedrock over direct OpenAI APIs?",
            "a": "Because hyperscaler platforms guarantee strict Data Residency (data never leaves specific regional data centers), Zero Data Retention (prompts are never logged or used for model training), and private VPC network isolation that complies with HIPAA, SOC2, and FedRAMP mandates."
          },
          {
            "q": "How does authentication differ between public OpenAI SDK and Azure OpenAI SDK?",
            "a": "Public OpenAI requires an API key in the Authorization header. Azure OpenAI uses Azure Active Directory (Microsoft Entra ID) Managed Identities or Service Principals, obtaining temporary OAuth2 access tokens without static secrets."
          }
        ],
        "checklist": [
          "Authenticated to Azure OpenAI using Managed Identities / Service Principals without static API keys.",
          "Invoked AWS Bedrock Claude 3.5 models using boto3 with IAM role-based access control.",
          "Configured private endpoints and data residency boundaries across cloud providers."
        ]
      },
      "projectVideos": [
        {
          "title": "End To End Advanced RAG App Using AWS Bedrock And Langchain",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=0LE5XrxGvbo",
          "duration": "37m",
          "why": "Strong AWS Bedrock RAG project using familiar Python/LangChain tooling.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Build a RAG based Generative AI Chatbot in 20 mins using Amazon Bedrock Knowledge Base",
          "creator": "Amazon Web Services",
          "url": "https://www.youtube.com/watch?v=hnyDDfo8e9Q",
          "duration": "11m",
          "why": "Official managed Bedrock Knowledge Base RAG project.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Complete End To End Generative AI Project On AWS Using AWS Bedrock And AWS Lambda",
          "creator": "Krish Naik",
          "url": "https://www.youtube.com/watch?v=3OP39y4dO_Y",
          "duration": "55m",
          "why": "AWS Lambda plus Bedrock project for cloud-native enterprise delivery.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 19,
      "name": "Scaling, Cost Optimization, Security And Enterprise AI Architecture",
      "time": "Time: 10-12h. Cumulative: 220h.",
      "videos": [
        {
          "order": 1,
          "title": "OWASP's Top 10 Ways to Attack LLMs",
          "creator": "IBM Technology",
          "url": "https://www.youtube.com/watch?v=gUNXZMcd2jU",
          "id": "gUNXZMcd2jU",
          "duration": "25m",
          "difficulty": "Medium",
          "why": "Security risks from a credible enterprise technology source.",
          "skip": "None."
        },
        {
          "order": 2,
          "title": "Explained: The OWASP Top 10 for Large Language Model Applications",
          "creator": "IBM Technology",
          "url": "https://www.youtube.com/watch?v=cYuesqIKf9A",
          "id": "cYuesqIKf9A",
          "duration": "14m",
          "difficulty": "Medium",
          "why": "Concise risk taxonomy for interviews and architecture reviews.",
          "skip": "If watched the previous IBM video, skim for terminology."
        },
        {
          "order": 3,
          "title": "What Is a Prompt Injection Attack?",
          "creator": "IBM Technology",
          "url": "https://www.youtube.com/watch?v=jrHRe9lSqqA",
          "id": "jrHRe9lSqqA",
          "duration": "11m",
          "difficulty": "Easy",
          "why": "Clear threat primer for enterprise AI security.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Beyond Simple RAG: Quality, Scale and Cost-Efficient Retrieval",
          "creator": "Databricks",
          "url": "https://www.youtube.com/watch?v=pUKvTs6Eg4k",
          "id": "pUKvTs6Eg4k",
          "duration": "37m",
          "difficulty": "Advanced",
          "why": "Enterprise-grade retrieval architecture from a credible platform team.",
          "skip": "Vendor-specific details optional."
        }
      ],
      "desktop": {
        "id": 19,
        "part": 4,
        "partName": "PART 4: Production Architecture & Portfolio Systems",
        "title": "Scaling, Cost Optimization, Security And Enterprise AI Architecture",
        "time": "8 Hours",
        "cumTime": "200h (90.9%)",
        "techs": [
          "Enterprise AI Architecture",
          "Design Patterns",
          "Dual-LLM Pattern",
          "EAI Routing",
          "Zero-Trust AI"
        ],
        "goal": "Synthesize all 48 previous topics into standardized enterprise architectural blueprints. Master the 5 core AI design patterns: Routing, Parallelization, Orchestration-Subagents, Evaluator-Optimizer, and Dual-LLM Privilege Separation.",
        "whyMatters": "As an AI Architect, you must present rigorous, fault-tolerant system designs to CTOs and architectural review boards. Knowing standard design patterns ensures your systems are maintainable, auditable, and scalable.",
        "javaAnalogy": "This is Gang of Four (GoF) design patterns and Martin Fowler's Enterprise Integration Patterns applied to non-deterministic AI cognitive engines.",
        "videos": [
          {
            "step": "19.1",
            "title": "How to Build Reliable AI Agents (without the hype)",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=T1Lowy1mnEg",
            "duration": "27m 48s",
            "difficulty": "Advanced",
            "whyBest": "Reliability-first agent architecture lesson that fits production AI engineering and FDE work.",
            "skip": "None.",
            "id": "T1Lowy1mnEg"
          },
          {
            "step": "19.2",
            "title": "The ONLY AI Tech Stack You Need in 2026",
            "creator": "Cole Medin",
            "url": "https://www.youtube.com/watch?v=21_k2St8bBI",
            "duration": "34m 25s",
            "difficulty": "Intermediate",
            "whyBest": "Current practical AI stack overview from a builder; useful for choosing deployable tools without generic system-design refreshers.",
            "skip": "Skip tools you have already implemented.",
            "id": "21_k2St8bBI"
          }
        ],
        "miniProject": "Architectural Blueprint Design: Create a comprehensive Mermaid.js diagram and technical RFC markdown document detailing a zero-trust internal AI search platform for 10,000 enterprise users.",
        "prodProject": "The Dual-LLM Privilege Separation Engine: Implement an enterprise security pattern where a high-privileged 'Executor LLM' (with access to SQL and APIs) never directly reads untrusted external data. A low-privileged 'Quarantine LLM' reads and sanitizes external web/email content, stripping executable instructions before passing clean summaries to the Executor.",
        "repos": [
          {
            "name": "anthropic-ai/anthropic-cookbook",
            "desc": "Anthropic's official architectural patterns for building effective agents.",
            "url": "https://github.com/anthropic-ai/anthropic-cookbook"
          },
          {
            "name": "microsoft/semantic-kernel",
            "desc": "Microsoft SDK integrating enterprise C#/Python code with AI orchestration.",
            "url": "https://github.com/microsoft/semantic-kernel"
          }
        ],
        "docs": [
          {
            "name": "Anthropic Building Effective Agents Guide",
            "url": "https://www.anthropic.com/research/building-effective-agents"
          },
          {
            "name": "Microsoft AI Architectural Patterns",
            "url": "https://learn.microsoft.com/en-us/azure/architecture/guide/ai/ai-architecture"
          }
        ],
        "mistakes": [
          "Building complex autonomous multi-agent loops for simple deterministic tasks that could be solved with a 5-line SQL query or basic script.",
          "Coupling frontend user web interfaces directly to vector databases without an intermediate API gateway security tier."
        ],
        "interviewQA": [
          {
            "q": "What is the Evaluator-Optimizer agent architecture pattern?",
            "a": "In this pattern, one LLM (the Optimizer) generates a solution (e.g. code or legal analysis), and a second LLM (the Evaluator) acts as a strict QA reviewer, grading the output against explicit criteria. If validation fails, the Evaluator feeds specific error feedback back to the Optimizer in a cyclic refinement loop until quality standards are met."
          },
          {
            "q": "When designing an enterprise AI system, how do you decide between RAG, Fine-Tuning, and Long Context Windows?",
            "a": "Use RAG when knowledge changes frequently, requires strict RBAC database permissions, or requires verifiable citations. Use Fine-Tuning when you need the model to adopt a specific communication tone, domain vocabulary, or structured output format. Use Long Context Windows for one-off document analysis (e.g. summarizing a 100-page contract) where building a persistent vector index is unnecessary."
          }
        ],
        "checklist": [
          "Mastered the 5 core AI agent architectural patterns (Routing, Parallelization, Orchestrator, Evaluator, Dual-LLM).",
          "Authored enterprise technical RFCs and architecture diagrams for AI system reviews.",
          "Implemented privilege separation architectures to isolate untrusted data ingestion from execution tools."
        ]
      },
      "projectVideos": [
        {
          "title": "AI Chatbot Architecture: MVP to Enterprise (With RAG, Microservices & Scaling Tips)",
          "creator": "Swarnendu De",
          "url": "https://www.youtube.com/watch?v=o7uMZkuegEE",
          "duration": "13m",
          "why": "Good system-design walkthrough for evolving an AI chatbot into an enterprise platform.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    },
    {
      "num": 20,
      "name": "End-To-End Capstone Project",
      "time": "Time: 20-30h. Cumulative: 240-250h.",
      "videos": [
        {
          "order": 1,
          "title": "How to Build a Production-Ready RAG AI Agent in Python",
          "creator": "Tech With Tim",
          "url": "https://www.youtube.com/watch?v=AUQJ9eeP-Ls",
          "id": "AUQJ9eeP-Ls",
          "duration": "1h 16m",
          "difficulty": "Advanced",
          "why": "End-to-end production-flavored build.",
          "skip": "Local model details optional."
        },
        {
          "order": 2,
          "title": "How AI Agents Actually Work (Explained in One Python File)",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=Q3Gb7Rjre3U",
          "id": "Q3Gb7Rjre3U",
          "duration": "32m",
          "difficulty": "Medium",
          "why": "Final agent mental model reset.",
          "skip": "None."
        },
        {
          "order": 3,
          "title": "How to Build Human-in-the-Loop for AI Agents",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=7GOxUgVTz3s",
          "id": "7GOxUgVTz3s",
          "duration": "25m",
          "difficulty": "Advanced",
          "why": "Enterprise-grade approval pattern.",
          "skip": "None."
        },
        {
          "order": 4,
          "title": "Building AI agents with Structured Outputs, Function Calling, and MCP",
          "creator": "Devoxx",
          "url": "https://www.youtube.com/watch?v=HmneQx1maCI",
          "id": "HmneQx1maCI",
          "duration": "49m",
          "difficulty": "Advanced",
          "why": "Ties together agent contracts, tools, and MCP.",
          "skip": "Conference intro optional."
        }
      ],
      "desktop": {
        "id": 20,
        "part": 4,
        "partName": "PART 4: Production Architecture & Portfolio Systems",
        "title": "End-To-End Capstone Project",
        "time": "20 Hours",
        "cumTime": "220h (100.0%)",
        "techs": [
          "End-to-End Capstone Project",
          "Full-Stack AI",
          "Production Portfolio Systems",
          "Master Portfolio"
        ],
        "goal": "Architect, code, deploy, and benchmark 10 production-grade enterprise AI systems from scratch. These capstones serve as your definitive master portfolio, proving your transition from Senior Java Developer to Elite Forward Deployed AI Engineer.",
        "whyMatters": "Theory is meaningless without implementation. Building these 10 end-to-end systems guarantees you can walk into any Principal AI Architect or Forward Deployed Engineer interview and demonstrate mastery over every layer of the AI stack.",
        "javaAnalogy": "This is your Spring Boot Master Architect certification thesis—building 10 complete, cloud-native distributed systems from the ground up.",
        "videos": [
          {
            "step": "20.1",
            "title": "Build a Complete End-to-End GenAI Project in 3 Hours",
            "creator": "Dave Ebbelaar",
            "url": "https://www.youtube.com/watch?v=E8zpgNPx8jE",
            "duration": "2h 58m 27s",
            "difficulty": "Advanced",
            "whyBest": "Practical end-to-end GenAI implementation from a trusted AI engineering creator; better fit than generic frontend-heavy SaaS builds.",
            "skip": "Skip UI polish and focus on backend, AI workflow, deployment, and evaluation.",
            "id": "E8zpgNPx8jE"
          },
          {
            "step": "20.2",
            "title": "Top RAG Interview Questions & Answers for AI Engineers (2026)",
            "creator": "santosh dataclass",
            "url": "https://www.youtube.com/watch?v=SOj60eheX9A",
            "duration": "19m",
            "difficulty": "Intermediate",
            "whyBest": "Targeted interview prep for RAG-heavy AI Engineer roles; use as a final readiness check.",
            "skip": "Skip questions that repeat your completed modules.",
            "id": "SOj60eheX9A"
          }
        ],
        "miniProject": "Portfolio Setup: Initialize a GitHub organization or mono-repo with clean CI/CD pipelines, pre-commit linting hooks, Docker compose environments, and unified documentation templates for your 10 capstones.",
        "prodProject": "10 Enterprise Capstone Architectures (See detailed specification below)",
        "repos": [
          {
            "name": "daveebbelaar/python-ai-saas-template",
            "desc": "Production-ready boilerplate for enterprise full-stack AI SaaS applications.",
            "url": "https://github.com/daveebbelaar"
          },
          {
            "name": "colemedin/ai-agent-enterprise-patterns",
            "desc": "Reference implementations of advanced multi-agent enterprise architectures.",
            "url": "https://github.com/colemedin"
          }
        ],
        "docs": [
          {
            "name": "Google Cloud AI Architecture Reference Guide",
            "url": "https://cloud.google.com/architecture/ai-ml"
          },
          {
            "name": "AWS Enterprise AI Lens Well-Architected Framework",
            "url": "https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/machine-learning-lens.html"
          }
        ],
        "mistakes": [
          "Building toy capstones with hardcoded API keys and zero error handling instead of production Dockerized services.",
          "Failing to include automated benchmark reports and latency metrics in your capstone README documentation."
        ],
        "interviewQA": [
          {
            "q": "How would you design a fault-tolerant, multi-region enterprise AI search platform for 50,000 employees?",
            "a": "Use a global CDN/API Gateway terminating OAuth2/JWT. Route read requests to regional read-replica Qdrant vector database clusters with int8 scalar quantization. Ingest documents asynchronously via Redis/ARQ worker queues with Parent-Child chunking. Implement semantic caching in Redis to serve frequent queries in <5ms, and use a dual-LLM architecture with NeMo Guardrails to ensure PII compliance and zero hallucination."
          },
          {
            "q": "How do you explain the transition from Java Full Stack Developer to AI Forward Deployed Engineer in an interview?",
            "a": "Emphasize that AI engineering is 80% distributed backend engineering and 20% model orchestration. Highlight your mastery of Java design patterns, ACID database transactions, and microservice containerization, explaining how you translated those exact principles into asynchronous Python FastAPI endpoints, Pydantic type validation, LangGraph cyclic state loops, and HNSW vector indexing."
          }
        ],
        "checklist": [
          "Architected and deployed all 10 Enterprise Capstone systems to production or local Kubernetes clusters.",
          "Executed automated CI/CD evaluation suites proving >90% RAG accuracy and zero guardrail breaches.",
          "Completed the 220-hour curriculum and mastered the Java Rosetta Stone for AI Engineering!"
        ]
      },
      "projectVideos": [
        {
          "title": "Build a Full-Stack GenAI Project in 4 Hours (FastAPI, React, Supabase)",
          "creator": "Dave Ebbelaar",
          "url": "https://www.youtube.com/watch?v=qF5il_9IwME",
          "duration": "3h 52m",
          "why": "Full-stack AI SaaS capstone with FastAPI, React, and Supabase.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Build a Large Language Model AI Chatbot using Retrieval Augmented Generation",
          "creator": "IBM Technology",
          "url": "https://www.youtube.com/watch?v=XctooiH0moI",
          "duration": "3m",
          "why": "Short enterprise RAG chatbot reference to frame the capstone architecture.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        },
        {
          "title": "Build Your AI Coding Assistant: Next js, TypeScript, Gemini AI Tutorial",
          "creator": "Dipesh Malvia",
          "url": "https://www.youtube.com/watch?v=8LWXQYaXFms",
          "duration": "1h 25m",
          "why": "Useful capstone variant for an AI coding assistant product.",
          "cost": "Free/local or low-cost API; cloud optional where noted"
        }
      ]
    }
  ],
  "capstones": [
    {
      "id": "C1",
      "title": "1. Enterprise Knowledge Base & Document AI Platform",
      "desc": "A multi-tenant RAG platform for 50,000 employees. Features hybrid search (Qdrant + BM25), Cohere cross-encoder re-ranking, parent-child indexing, multimodal table parsing, and strict RBAC SQL permissions.",
      "techs": [
        "FastAPI",
        "Qdrant",
        "Cohere Rerank",
        "Unstructured",
        "PostgreSQL",
        "Docker"
      ]
    },
    {
      "id": "C2",
      "title": "2. Autonomous SQL & ERP Database Analyst Agent",
      "desc": "A natural language to SQL/Jira agent using LangGraph. Features AST SQL syntax sanitization (blocking DML/DDL), automatic retry loops on SQL syntax errors, and interactive chart generation.",
      "techs": [
        "LangGraph",
        "PostgreSQL",
        "Pydantic",
        "Instructor",
        "sqlparse",
        "FastAPI"
      ]
    },
    {
      "id": "C3",
      "title": "3. Self-Healing DevOps & CI/CD Autonomous Engineer",
      "desc": "An agent connected to GitHub webhooks and MCP servers. Inspects failing build logs, reproduces bugs in a Dockerized sandbox, applies code refactoring, runs pytest, and submits PR review comments.",
      "techs": [
        "Model Context Protocol (MCP)",
        "LangGraph",
        "Docker SDK",
        "GitHub API",
        "Pytest"
      ]
    },
    {
      "id": "C4",
      "title": "4. Multi-Channel Customer Support AI Platform",
      "desc": "An n8n visual automation platform integrating Zendesk, Slack, and email webhooks. Routes queries via Redis semantic caching, retrieves troubleshooting manuals from Weaviate, and escalates complex issues to human agents.",
      "techs": [
        "n8n AI Workflows",
        "Weaviate",
        "Redis",
        "Zendesk API",
        "Slack Webhooks"
      ]
    },
    {
      "id": "C5",
      "title": "5. Real-Time Financial News & Earnings Call Analyst Crew",
      "desc": "A 4-agent CrewAI system (Scraper, Financial Analyst, Risk Assessor, Executive Writer). Ingests quarterly earnings PDFs and SEC filings, performs sentiment analysis, and outputs structured financial audit reports.",
      "techs": [
        "CrewAI",
        "LlamaParse",
        "OpenAI Strict Schema",
        "Pydantic",
        "Apache Parquet"
      ]
    },
    {
      "id": "C6",
      "title": "6. Enterprise Code Reviewer & Security Auditing Suite",
      "desc": "A specialized multi-agent system analyzing corporate codebases against OWASP Top 10 vulnerabilities. Uses tree-sitter for syntax tree parsing, flags SQL injections, and suggests automated fixes.",
      "techs": [
        "AG2 / AutoGen",
        "Tree-Sitter",
        "FastAPI",
        "LangSmith",
        "OpenTelemetry"
      ]
    },
    {
      "id": "C7",
      "title": "7. AI-Powered Contract & Legal Compliance Analyzer",
      "desc": "A high-precision legal comparison engine using LlamaIndex. Ingests 100-page vendor MSAs, compares them against internal corporate legal standards using SubQuestionQueryEngine, and highlights liability risks.",
      "techs": [
        "LlamaIndex",
        "ChromaDB",
        "FastAPI",
        "DeepEval",
        "NeMo Guardrails"
      ]
    },
    {
      "id": "C8",
      "title": "8. Distributed AI Gateway & Intelligent Cost Router",
      "desc": "An enterprise API proxy replacing direct LLM integrations. Features JWT auth, sliding window rate limits, Redis semantic caching, automatic failover across Azure/AWS/Vertex, and tenant budgeting.",
      "techs": [
        "FastAPI",
        "Redis",
        "Azure OpenAI",
        "AWS Bedrock",
        "Vertex AI",
        "Celery"
      ]
    },
    {
      "id": "C9",
      "title": "9. Multimodal Video & Voice AI Meeting Summarizer",
      "desc": "An async background processing pipeline using ARQ and Celery. Ingests 2-hour Zoom recording MP4s, transcribes via Whisper, feeds multimodal frames into a current Gemini multimodal model, and extracts action items into Jira.",
      "techs": [
        "Gemini 1.5 Pro",
        "ARQ",
        "Redis",
        "FFmpeg",
        "Jira API",
        "Docker"
      ]
    },
    {
      "id": "C10",
      "title": "10. Zero-Trust Internal Company AI Search Portal",
      "desc": "A complete full-stack portal with Next.js frontend and FastAPI Kubernetes backend. Features SSE streaming, Langfuse tracing, NeMo Guardrails injection defense, and multi-tenant namespace partitioning.",
      "techs": [
        "Kubernetes",
        "FastAPI",
        "Langfuse",
        "NeMo Guardrails",
        "pgvector",
        "SSE"
      ]
    }
  ],
  "rosetta": [
    {
      "java": "Spring Framework / Spring Boot",
      "ai": "LangChain / LlamaIndex / LangGraph",
      "why": "High-level orchestration frameworks providing DI, abstractions over underlying providers, and pre-built integration adapters. Hides raw HTTP boilerplate just like Spring hides JDBC."
    },
    {
      "java": "Jackson / Lombok / Java Records",
      "ai": "Pydantic / Instructor / Structured Outputs",
      "why": "Data validation and schema enforcement. When an LLM returns unstructured markdown, Pydantic coerces and validates it into typed domain objects, failing fast just like @Valid in Spring MVC."
    },
    {
      "java": "Spring State Machine / BPMN (Camunda)",
      "ai": "LangGraph / CrewAI Workflows",
      "why": "Stateful, cyclic graph execution where nodes represent LLM reasoning/tools and edges represent deterministic or conditional transitions based on shared graph state."
    },
    {
      "java": "Spring Data JPA / Hibernate",
      "ai": "Vector DB Client SDKs / LlamaIndex Storage Context",
      "why": "Abstractions over database persistence. Instead of querying by B-Tree primary keys or SQL WHERE clauses, you query by High-Dimensional Approximate Nearest Neighbor (HNSW) cosine similarity."
    },
    {
      "java": "PostgreSQL / Oracle B-Tree Indexes",
      "ai": "HNSW / IVF Approximate Nearest Neighbor Indexes",
      "why": "Indexing structures built for speed. While B-Trees achieve O(log N) exact lookups, HNSW builds multi-layer skip-lists to achieve sub-millisecond approximate semantic similarity lookups across millions of vectors."
    },
    {
      "java": "Spring Security / OAuth2 / JWT",
      "ai": "NeMo Guardrails / Llama Guard / Auth for AI",
      "why": "Interceptors and API gateways that inspect inbound requests (for prompt injections/jailbreaks) and outbound responses (for PII leakage, toxicity, or hallucination) before reaching the client."
    },
    {
      "java": "Spring Cloud Sleuth / Micrometer / Zipkin",
      "ai": "LangSmith / Langfuse / OpenTelemetry for AI",
      "why": "Distributed tracing and observability. Tracks the lifecycle of an AI request across multi-step agent graphs, recording token consumption, latency per span, and cost attribution per tenant."
    },
    {
      "java": "RabbitMQ / Kafka / Spring Cloud Stream",
      "ai": "Celery / ARQ / Background Workers (Redis)",
      "why": "Asynchronous execution queues. Heavy LLM generation, document ingestion, and embedding generation must never block the synchronous HTTP request thread pool."
    },
    {
      "java": "Server-Sent Events (SSE) / WebFlux",
      "ai": "FastAPI Streaming Responses (Async Generators)",
      "why": "Delivering token-by-token chunks to the frontend UI to reduce perceived Time-To-First-Token (TTFT) latency from 4 seconds down to 300 milliseconds."
    },
    {
      "java": "REST Template / WebClient / Feign",
      "ai": "OpenAI / Anthropic / Gemini Official Python SDKs",
      "why": "Strongly-typed HTTP clients that handle connection pooling, automatic retries with exponential backoff, rate limit handling (HTTP 429), and streaming chunk assembly."
    }
  ],
  "repos": [
    {
      "name": "pydantic/pydantic",
      "desc": "Industry standard data validation. Study BaseClass and JSON serialization.",
      "url": "https://github.com/pydantic/pydantic",
      "moduleId": 1,
      "moduleTitle": "Python for Enterprise Engineers"
    },
    {
      "name": "encode/httpx",
      "desc": "Next-gen async HTTP client for Python connection pooling.",
      "url": "https://github.com/encode/httpx",
      "moduleId": 1,
      "moduleTitle": "Python for Enterprise Engineers"
    },
    {
      "name": "openai/openai-python",
      "desc": "Official OpenAI Python SDK repository.",
      "url": "https://github.com/openai/openai-python",
      "moduleId": 2,
      "moduleTitle": "Foundation APIs (OpenAI, Anthropic, Gemini)"
    },
    {
      "name": "BerriAI/litellm",
      "desc": "Industry standard library for calling 100+ LLM APIs using unified OpenAI format.",
      "url": "https://github.com/BerriAI/litellm",
      "moduleId": 2,
      "moduleTitle": "Foundation APIs (OpenAI, Anthropic, Gemini)"
    },
    {
      "name": "jxnl/instructor",
      "desc": "Gold standard library for structured Pydantic data extraction across any LLM.",
      "url": "https://github.com/jxnl/instructor",
      "moduleId": 3,
      "moduleTitle": "Prompt Engineering, Structured Outputs & Tool Calling"
    },
    {
      "name": "pydantic/pydantic-ai",
      "desc": "Pydantic official type-safe agent framework with DI support.",
      "url": "https://github.com/pydantic/pydantic-ai",
      "moduleId": 3,
      "moduleTitle": "Prompt Engineering, Structured Outputs & Tool Calling"
    },
    {
      "name": "UKPLab/sentence-transformers",
      "desc": "Industry standard framework for local text embedding models.",
      "url": "https://github.com/UKPLab/sentence-transformers",
      "moduleId": 4,
      "moduleTitle": "Embeddings & Latent Space Representation"
    },
    {
      "name": "qdrant/fastembed",
      "desc": "Fast ONNX-based Python embedding library without PyTorch bloat.",
      "url": "https://github.com/qdrant/fastembed",
      "moduleId": 4,
      "moduleTitle": "Embeddings & Latent Space Representation"
    },
    {
      "name": "qdrant/qdrant",
      "desc": "High-performance Rust vector database with scalar quantization.",
      "url": "https://github.com/qdrant/qdrant",
      "moduleId": 5,
      "moduleTitle": "Enterprise Vector Databases"
    },
    {
      "name": "weaviate/weaviate",
      "desc": "Cloud-native AI database with GraphQL and hybrid BM25.",
      "url": "https://github.com/weaviate/weaviate",
      "moduleId": 5,
      "moduleTitle": "Enterprise Vector Databases"
    },
    {
      "name": "run-llama/llama_index",
      "desc": "Leading enterprise data framework for document indexing and retrieval.",
      "url": "https://github.com/run-llama/llama_index",
      "moduleId": 6,
      "moduleTitle": "RAG Fundamentals & Data Pipelines"
    },
    {
      "name": "Unstructured-IO/unstructured",
      "desc": "Open-source document extraction library for complex PDFs and HTML.",
      "url": "https://github.com/Unstructured-IO/unstructured",
      "moduleId": 6,
      "moduleTitle": "RAG Fundamentals & Data Pipelines"
    },
    {
      "name": "cohere-ai/cohere-python",
      "desc": "Official SDK for industry-leading cross-encoder re-ranking models.",
      "url": "https://github.com/cohere-ai/cohere-python",
      "moduleId": 7,
      "moduleTitle": "Advanced RAG, Hybrid Search & Re-ranking"
    },
    {
      "name": "tavily-ai/tavily-python",
      "desc": "Search API built for RAG fallback pipelines returning clean markdown.",
      "url": "https://github.com/tavily-ai/tavily-python",
      "moduleId": 7,
      "moduleTitle": "Advanced RAG, Hybrid Search & Re-ranking"
    },
    {
      "name": "langchain-ai/langchain",
      "desc": "The foundational framework for building LLM applications.",
      "url": "https://github.com/langchain-ai/langchain",
      "moduleId": 8,
      "moduleTitle": "Orchestration Frameworks (LangChain & LlamaIndex)"
    },
    {
      "name": "langchain-ai/langgraph",
      "desc": "Library for building stateful, multi-actor applications with LLMs.",
      "url": "https://github.com/langchain-ai/langgraph",
      "moduleId": 9,
      "moduleTitle": "Stateful Workflows (LangGraph & MCP)"
    },
    {
      "name": "modelcontextprotocol/python-sdk",
      "desc": "Official Python SDK for the Model Context Protocol (MCP).",
      "url": "https://github.com/modelcontextprotocol/python-sdk",
      "moduleId": 9,
      "moduleTitle": "Stateful Workflows (LangGraph & MCP)"
    },
    {
      "name": "joaomdmoura/crewAI",
      "desc": "Leading framework for orchestrating autonomous AI agent crews.",
      "url": "https://github.com/joaomdmoura/crewAI",
      "moduleId": 10,
      "moduleTitle": "Autonomous Agents & Multi-Agent Systems"
    },
    {
      "name": "ag2ai/ag2",
      "desc": "Microsoft's evolutionary continuation of AutoGen for multi-agent conversations.",
      "url": "https://github.com/ag2ai/ag2",
      "moduleId": 10,
      "moduleTitle": "Autonomous Agents & Multi-Agent Systems"
    },
    {
      "name": "n8n-io/n8n",
      "desc": "Fair-code licensed workflow automation tool with native AI nodes.",
      "url": "https://github.com/n8n-io/n8n",
      "moduleId": 11,
      "moduleTitle": "Enterprise No-Code/Low-Code AI Workflows (n8n)"
    },
    {
      "name": "n8n-io/self-hosted-ai-starter-kit",
      "desc": "Docker Compose starter kit for self-hosting n8n + Qdrant + Postgres + Ollama.",
      "url": "https://github.com/n8n-io/self-hosted-ai-starter-kit",
      "moduleId": 11,
      "moduleTitle": "Enterprise No-Code/Low-Code AI Workflows (n8n)"
    },
    {
      "name": "langfuse/langfuse",
      "desc": "Open-source LLM engineering platform: tracing, evaluations, prompt management.",
      "url": "https://github.com/langfuse/langfuse",
      "moduleId": 12,
      "moduleTitle": "AI Observability Part 1 (LangSmith & Langfuse)"
    },
    {
      "name": "langchain-ai/langsmith-sdk",
      "desc": "Official Python SDK for LangSmith distributed tracing.",
      "url": "https://github.com/langchain-ai/langsmith-sdk",
      "moduleId": 12,
      "moduleTitle": "AI Observability Part 1 (LangSmith & Langfuse)"
    },
    {
      "name": "tiangolo/fastapi",
      "desc": "Modern, fast web framework for building APIs with Python 3.8+.",
      "url": "https://github.com/tiangolo/fastapi",
      "moduleId": 13,
      "moduleTitle": "High-Performance AI Backends (FastAPI, Docker, K8s)"
    },
    {
      "name": "astral-sh/uv-docker-example",
      "desc": "Official best practices for containerizing fast Python uv apps in Docker.",
      "url": "https://github.com/astral-sh/uv-docker-example",
      "moduleId": 13,
      "moduleTitle": "High-Performance AI Backends (FastAPI, Docker, K8s)"
    },
    {
      "name": "pgvector/pgvector",
      "desc": "Open-source vector similarity search for PostgreSQL.",
      "url": "https://github.com/pgvector/pgvector",
      "moduleId": 14,
      "moduleTitle": "Enterprise Data Tier for AI (PostgreSQL pgvector, Redis)"
    },
    {
      "name": "redis/redis-py",
      "desc": "Official Python client for Redis with native vector search support.",
      "url": "https://github.com/redis/redis-py",
      "moduleId": 14,
      "moduleTitle": "Enterprise Data Tier for AI (PostgreSQL pgvector, Redis)"
    },
    {
      "name": "Azure/azure-sdk-for-python",
      "desc": "Official Azure SDK for Python containing azure-ai-openai client.",
      "url": "https://github.com/Azure/azure-sdk-for-python",
      "moduleId": 15,
      "moduleTitle": "Cloud AI Hyperscalers (Azure AI, AWS Bedrock, Vertex)"
    },
    {
      "name": "boto/boto3",
      "desc": "Official AWS SDK for Python (Boto3) for invoking Bedrock models.",
      "url": "https://github.com/boto/boto3",
      "moduleId": 15,
      "moduleTitle": "Cloud AI Hyperscalers (Azure AI, AWS Bedrock, Vertex)"
    },
    {
      "name": "samuelcolvin/arq",
      "desc": "Fast async job queues for Python built on Redis and asyncio.",
      "url": "https://github.com/samuelcolvin/arq",
      "moduleId": 16,
      "moduleTitle": "Advanced Ops (Auth, Streaming Responses, Background Workers)"
    },
    {
      "name": "sysid/sse-starlette",
      "desc": "Server-Sent Events (SSE) support for Starlette and FastAPI.",
      "url": "https://github.com/sysid/sse-starlette",
      "moduleId": 16,
      "moduleTitle": "Advanced Ops (Auth, Streaming Responses, Background Workers)"
    },
    {
      "name": "explodinggradients/ragas",
      "desc": "Evaluation framework for Retrieval Augmented Generation (RAG) pipelines.",
      "url": "https://github.com/explodinggradients/ragas",
      "moduleId": 17,
      "moduleTitle": "AI Safety & Quality (Evaluation, Guardrails)"
    },
    {
      "name": "NVIDIA/NeMo-Guardrails",
      "desc": "Open-source toolkit for easily adding programmable guardrails to LLM systems.",
      "url": "https://github.com/NVIDIA/NeMo-Guardrails",
      "moduleId": 17,
      "moduleTitle": "AI Safety & Quality (Evaluation, Guardrails)"
    },
    {
      "name": "OWASP/www-project-top-10-for-large-language-model-applications",
      "desc": "Official OWASP security guidance for LLM applications.",
      "url": "https://github.com/OWASP/www-project-top-10-for-large-language-model-applications",
      "moduleId": 18,
      "moduleTitle": "Production Ops (Deployment, Scaling, Cost, Security)"
    },
    {
      "name": "vllm-project/vllm",
      "desc": "High-throughput and memory-efficient LLM serving engine with PagedAttention.",
      "url": "https://github.com/vllm-project/vllm",
      "moduleId": 18,
      "moduleTitle": "Production Ops (Deployment, Scaling, Cost, Security)"
    },
    {
      "name": "anthropic-ai/anthropic-cookbook",
      "desc": "Anthropic's official architectural patterns for building effective agents.",
      "url": "https://github.com/anthropic-ai/anthropic-cookbook",
      "moduleId": 19,
      "moduleTitle": "Enterprise AI Architecture & Design Patterns"
    },
    {
      "name": "microsoft/semantic-kernel",
      "desc": "Microsoft SDK integrating enterprise C#/Python code with AI orchestration.",
      "url": "https://github.com/microsoft/semantic-kernel",
      "moduleId": 19,
      "moduleTitle": "Enterprise AI Architecture & Design Patterns"
    },
    {
      "name": "daveebbelaar/python-ai-saas-template",
      "desc": "Production-ready boilerplate for enterprise full-stack AI SaaS applications.",
      "url": "https://github.com/daveebbelaar",
      "moduleId": 20,
      "moduleTitle": "The Grand Capstone Bootcamp (10 Enterprise Systems)"
    },
    {
      "name": "colemedin/ai-agent-enterprise-patterns",
      "desc": "Reference implementations of advanced multi-agent enterprise architectures.",
      "url": "https://github.com/colemedin",
      "moduleId": 20,
      "moduleTitle": "The Grand Capstone Bootcamp (10 Enterprise Systems)"
    }
  ],
  "docs": [
    {
      "name": "Pydantic v2 Official Docs",
      "url": "https://docs.pydantic.dev/latest/",
      "moduleId": 1,
      "moduleTitle": "Python for Enterprise Engineers"
    },
    {
      "name": "Python Asyncio Docs",
      "url": "https://docs.python.org/3/library/asyncio.html",
      "moduleId": 1,
      "moduleTitle": "Python for Enterprise Engineers"
    },
    {
      "name": "OpenAI API Reference",
      "url": "https://platform.openai.com/docs/api-reference",
      "moduleId": 2,
      "moduleTitle": "Foundation APIs (OpenAI, Anthropic, Gemini)"
    },
    {
      "name": "Anthropic Prompt Caching Docs",
      "url": "https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching",
      "moduleId": 2,
      "moduleTitle": "Foundation APIs (OpenAI, Anthropic, Gemini)"
    },
    {
      "name": "OpenAI Structured Outputs Guide",
      "url": "https://platform.openai.com/docs/guides/structured-outputs",
      "moduleId": 3,
      "moduleTitle": "Prompt Engineering, Structured Outputs & Tool Calling"
    },
    {
      "name": "Anthropic Tool Use (Function Calling) Docs",
      "url": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use",
      "moduleId": 3,
      "moduleTitle": "Prompt Engineering, Structured Outputs & Tool Calling"
    },
    {
      "name": "MTEB Embedding Benchmark Leaderboard",
      "url": "https://huggingface.co/spaces/mteb/leaderboard",
      "moduleId": 4,
      "moduleTitle": "Embeddings & Latent Space Representation"
    },
    {
      "name": "OpenAI Embeddings Guide",
      "url": "https://platform.openai.com/docs/guides/embeddings",
      "moduleId": 4,
      "moduleTitle": "Embeddings & Latent Space Representation"
    },
    {
      "name": "Qdrant Documentation & HNSW Tuning",
      "url": "https://qdrant.tech/documentation/",
      "moduleId": 5,
      "moduleTitle": "Enterprise Vector Databases"
    },
    {
      "name": "Pinecone Serverless Docs",
      "url": "https://docs.pinecone.io/guides/get-started/overview",
      "moduleId": 5,
      "moduleTitle": "Enterprise Vector Databases"
    },
    {
      "name": "LlamaIndex Understanding RAG",
      "url": "https://docs.llamaindex.ai/en/stable/understanding/rag/",
      "moduleId": 6,
      "moduleTitle": "RAG Fundamentals & Data Pipelines"
    },
    {
      "name": "Unstructured.io API Docs",
      "url": "https://docs.unstructured.io/",
      "moduleId": 6,
      "moduleTitle": "RAG Fundamentals & Data Pipelines"
    },
    {
      "name": "Pinecone Hybrid Search Guide",
      "url": "https://docs.pinecone.io/guides/data/understand-hybrid-search",
      "moduleId": 7,
      "moduleTitle": "Advanced RAG, Hybrid Search & Re-ranking"
    },
    {
      "name": "Cohere Rerank Documentation",
      "url": "https://docs.cohere.com/docs/rerank-overview",
      "moduleId": 7,
      "moduleTitle": "Advanced RAG, Hybrid Search & Re-ranking"
    },
    {
      "name": "LangChain LCEL Conceptual Guide",
      "url": "https://python.langchain.com/docs/concepts/lcel/",
      "moduleId": 8,
      "moduleTitle": "Orchestration Frameworks (LangChain & LlamaIndex)"
    },
    {
      "name": "LlamaIndex Query Engine Docs",
      "url": "https://docs.llamaindex.ai/en/stable/module_guides/deploying/query_engine/",
      "moduleId": 8,
      "moduleTitle": "Orchestration Frameworks (LangChain & LlamaIndex)"
    },
    {
      "name": "LangGraph Official Tutorial",
      "url": "https://langchain-ai.github.io/langgraph/",
      "moduleId": 9,
      "moduleTitle": "Stateful Workflows (LangGraph & MCP)"
    },
    {
      "name": "Model Context Protocol Specification",
      "url": "https://modelcontextprotocol.io/introduction",
      "moduleId": 9,
      "moduleTitle": "Stateful Workflows (LangGraph & MCP)"
    },
    {
      "name": "CrewAI Core Concepts Docs",
      "url": "https://docs.crewai.com/core-concepts/Crews/",
      "moduleId": 10,
      "moduleTitle": "Autonomous Agents & Multi-Agent Systems"
    },
    {
      "name": "AG2 / AutoGen User Guide",
      "url": "https://docs.ag2.ai/docs/getting-started",
      "moduleId": 10,
      "moduleTitle": "Autonomous Agents & Multi-Agent Systems"
    },
    {
      "name": "n8n Advanced AI Node Documentation",
      "url": "https://docs.n8n.io/advanced-ai/",
      "moduleId": 11,
      "moduleTitle": "Enterprise No-Code/Low-Code AI Workflows (n8n)"
    },
    {
      "name": "Self-Hosting n8n in Production Guide",
      "url": "https://docs.n8n.io/hosting/",
      "moduleId": 11,
      "moduleTitle": "Enterprise No-Code/Low-Code AI Workflows (n8n)"
    },
    {
      "name": "Langfuse Quickstart Guide",
      "url": "https://langfuse.com/docs/get-started",
      "moduleId": 12,
      "moduleTitle": "AI Observability Part 1 (LangSmith & Langfuse)"
    },
    {
      "name": "LangSmith Tracing Concepts",
      "url": "https://docs.smith.langchain.com/tracing",
      "moduleId": 12,
      "moduleTitle": "AI Observability Part 1 (LangSmith & Langfuse)"
    },
    {
      "name": "FastAPI Dependency Injection Guide",
      "url": "https://fastapi.tiangolo.com/tutorial/dependencies/",
      "moduleId": 13,
      "moduleTitle": "High-Performance AI Backends (FastAPI, Docker, K8s)"
    },
    {
      "name": "Kubernetes Managing GPUs Docs",
      "url": "https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/",
      "moduleId": 13,
      "moduleTitle": "High-Performance AI Backends (FastAPI, Docker, K8s)"
    },
    {
      "name": "pgvector HNSW Indexing Guide",
      "url": "https://github.com/pgvector/pgvector#hnsw",
      "moduleId": 14,
      "moduleTitle": "Enterprise Data Tier for AI (PostgreSQL pgvector, Redis)"
    },
    {
      "name": "Redis Vector Search Documentation",
      "url": "https://redis.io/docs/latest/develop/interact/search-and-query/basic-constructs/vector-fields/",
      "moduleId": 14,
      "moduleTitle": "Enterprise Data Tier for AI (PostgreSQL pgvector, Redis)"
    },
    {
      "name": "Azure OpenAI Service Architecture Guide",
      "url": "https://learn.microsoft.com/en-us/azure/ai-services/openai/overview",
      "moduleId": 15,
      "moduleTitle": "Cloud AI Hyperscalers (Azure AI, AWS Bedrock, Vertex)"
    },
    {
      "name": "AWS Bedrock API Reference",
      "url": "https://docs.aws.amazon.com/bedrock/latest/userguide/what-is-bedrock.html",
      "moduleId": 15,
      "moduleTitle": "Cloud AI Hyperscalers (Azure AI, AWS Bedrock, Vertex)"
    },
    {
      "name": "FastAPI OAuth2 with JWT Tokens Guide",
      "url": "https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/",
      "moduleId": 16,
      "moduleTitle": "Advanced Ops (Auth, Streaming Responses, Background Workers)"
    },
    {
      "name": "ARQ Async Job Queue Documentation",
      "url": "https://arq-docs.helpmanual.io/",
      "moduleId": 16,
      "moduleTitle": "Advanced Ops (Auth, Streaming Responses, Background Workers)"
    },
    {
      "name": "Ragas Metric Definitions & Math",
      "url": "https://docs.ragas.io/en/stable/concepts/metrics/index.html",
      "moduleId": 17,
      "moduleTitle": "AI Safety & Quality (Evaluation, Guardrails)"
    },
    {
      "name": "NVIDIA NeMo Guardrails Documentation",
      "url": "https://docs.nvidia.com/nemo/guardrails/index.html",
      "moduleId": 17,
      "moduleTitle": "AI Safety & Quality (Evaluation, Guardrails)"
    },
    {
      "name": "OWASP Top 10 for LLMs Official Site",
      "url": "https://owasp.org/www-project-top-10-for-large-language-model-applications/",
      "moduleId": 18,
      "moduleTitle": "Production Ops (Deployment, Scaling, Cost, Security)"
    },
    {
      "name": "vLLM PagedAttention Architecture",
      "url": "https://docs.vllm.ai/en/latest/models/engine_args.html",
      "moduleId": 18,
      "moduleTitle": "Production Ops (Deployment, Scaling, Cost, Security)"
    },
    {
      "name": "Anthropic Building Effective Agents Guide",
      "url": "https://www.anthropic.com/research/building-effective-agents",
      "moduleId": 19,
      "moduleTitle": "Enterprise AI Architecture & Design Patterns"
    },
    {
      "name": "Microsoft AI Architectural Patterns",
      "url": "https://learn.microsoft.com/en-us/azure/architecture/guide/ai/ai-architecture",
      "moduleId": 19,
      "moduleTitle": "Enterprise AI Architecture & Design Patterns"
    },
    {
      "name": "Google Cloud AI Architecture Reference Guide",
      "url": "https://cloud.google.com/architecture/ai-ml",
      "moduleId": 20,
      "moduleTitle": "The Grand Capstone Bootcamp (10 Enterprise Systems)"
    },
    {
      "name": "AWS Enterprise AI Lens Well-Architected Framework",
      "url": "https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/machine-learning-lens.html",
      "moduleId": 20,
      "moduleTitle": "The Grand Capstone Bootcamp (10 Enterprise Systems)"
    }
  ],
  "allVideos": [
    {
      "order": 1,
      "title": "Python for AI - Full Beginner Course",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=ygXn5nV5qFc",
      "id": "ygXn5nV5qFc",
      "duration": "5h 15m",
      "difficulty": "Easy",
      "why": "Best AI-focused Python path because it teaches the Python you need for LLM apps, not academic data science.",
      "skip": "None.",
      "moduleName": "Python For AI Engineers"
    },
    {
      "order": 2,
      "title": "Python Pydantic Tutorial: Complete Data Validation Course",
      "creator": "Corey Schafer",
      "url": "https://www.youtube.com/watch?v=M81pfi64eeM",
      "id": "M81pfi64eeM",
      "duration": "1h 29m",
      "difficulty": "Medium",
      "why": "High-quality hands-on Pydantic validation course from one of the strongest Python educators.",
      "skip": "None.",
      "moduleName": "Python For AI Engineers"
    },
    {
      "order": 3,
      "title": "Pydantic v2 Full Course - Python Data Validation",
      "creator": "ArjanCodes",
      "url": "https://www.youtube.com/watch?v=Vj-iU-8_xLs",
      "id": "Vj-iU-8_xLs",
      "duration": "45m",
      "difficulty": "Medium",
      "why": "Explains production-grade typed models, validation, JSON parsing, and clean architecture patterns.",
      "skip": "None.",
      "moduleName": "Python For AI Engineers"
    },
    {
      "order": 4,
      "title": "Asyncio in Python - Complete Tutorial for Backend Developers",
      "creator": "mCoding",
      "url": "https://www.youtube.com/watch?v=K56nNuBEd0c",
      "id": "K56nNuBEd0c",
      "duration": "35m",
      "difficulty": "Advanced",
      "why": "Best practical mental model for async API calls, concurrency, and non-blocking LLM gateways.",
      "skip": "Skip the low-level history if you already understand async/await.",
      "moduleName": "Python For AI Engineers"
    },
    {
      "order": 1,
      "title": "OpenAI Just Changed Everything (Responses API Walkthrough)",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=0pGxoubWI6s",
      "id": "0pGxoubWI6s",
      "duration": "29m",
      "difficulty": "Medium",
      "why": "Best current OpenAI application API walkthrough for production app builders.",
      "skip": "None.",
      "moduleName": "LLM API Fundamentals: OpenAI, Anthropic, Gemini"
    },
    {
      "order": 2,
      "title": "The OpenAI (Python) API - Introduction & Example Code",
      "creator": "Shaw Talebi",
      "url": "https://www.youtube.com/watch?v=czvVibB2lRA",
      "id": "czvVibB2lRA",
      "duration": "23m",
      "difficulty": "Easy",
      "why": "Clean Python SDK walkthrough without unnecessary theory.",
      "skip": "None.",
      "moduleName": "LLM API Fundamentals: OpenAI, Anthropic, Gemini"
    },
    {
      "order": 3,
      "title": "Getting Started with Tool Use in the Anthropic API",
      "creator": "Ram Vegiraju",
      "url": "https://www.youtube.com/watch?v=7xVmf9lIj14",
      "id": "7xVmf9lIj14",
      "duration": "14m 4s",
      "difficulty": "Medium",
      "why": "Newer Anthropic API-focused walkthrough for current Claude tool-use patterns, better than older model-specific Claude guides.",
      "skip": "None.",
      "moduleName": "LLM API Fundamentals: OpenAI, Anthropic, Gemini"
    },
    {
      "order": 4,
      "title": "The Gemini Interactions API",
      "creator": "Sam Witteveen",
      "url": "https://www.youtube.com/watch?v=aZgH_wnmedQ",
      "id": "aZgH_wnmedQ",
      "duration": "24m",
      "difficulty": "Medium",
      "why": "Better Gemini-native mental model than generic starter clips.",
      "skip": "None.",
      "moduleName": "LLM API Fundamentals: OpenAI, Anthropic, Gemini"
    },
    {
      "order": 5,
      "title": "Build agents with Gemini API (I/O Connect 2026)",
      "creator": "Google for Developers",
      "url": "https://www.youtube.com/watch?v=d9LAQWKUnx8",
      "id": "d9LAQWKUnx8",
      "duration": "37m 10s",
      "difficulty": "Medium",
      "why": "Current Google Gemini API agent walkthrough that is more relevant than older current Gemini long-context demos.",
      "skip": "Skip event intro if you only want implementation.",
      "moduleName": "LLM API Fundamentals: OpenAI, Anthropic, Gemini"
    },
    {
      "order": 1,
      "title": "Prompt Engineering Tutorial - Master ChatGPT and LLM Responses",
      "creator": "freeCodeCamp.org",
      "url": "https://www.youtube.com/watch?v=_ZvnD73m40o",
      "id": "_ZvnD73m40o",
      "duration": "42m",
      "difficulty": "Easy",
      "why": "Practical, broad, not math-heavy.",
      "skip": "Skip consumer productivity examples.",
      "moduleName": "Prompt Engineering, Structured Outputs, Tool Calling"
    },
    {
      "order": 2,
      "title": "OpenAI Structured Output - All You Need to Know",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=fuMKrKlaku4",
      "id": "fuMKrKlaku4",
      "duration": "25m",
      "difficulty": "Medium",
      "why": "Strong bridge from prompts to typed outputs.",
      "skip": "None.",
      "moduleName": "Prompt Engineering, Structured Outputs, Tool Calling"
    },
    {
      "order": 3,
      "title": "OpenAI Responses API: Structured Outputs with Pydantic",
      "creator": "Leon van Zyl",
      "url": "https://www.youtube.com/watch?v=3Z03fwH1I7s",
      "id": "3Z03fwH1I7s",
      "duration": "5m",
      "difficulty": "Easy",
      "why": "Quick Pydantic pattern reinforcement.",
      "skip": "None.",
      "moduleName": "Prompt Engineering, Structured Outputs, Tool Calling"
    },
    {
      "order": 4,
      "title": "OpenAI Function Calling - Full Beginner Tutorial",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=aqdWSYWC_LI",
      "id": "aqdWSYWC_LI",
      "duration": "28m",
      "difficulty": "Medium",
      "why": "Practical tool/function calling.",
      "skip": "None.",
      "moduleName": "Prompt Engineering, Structured Outputs, Tool Calling"
    },
    {
      "order": 5,
      "title": "What is Tool Calling? Connecting LLMs to Your Data",
      "creator": "IBM Technology",
      "url": "https://www.youtube.com/watch?v=h8gMhXYAv1k",
      "id": "h8gMhXYAv1k",
      "duration": "5m",
      "difficulty": "Easy",
      "why": "Crisp conceptual explanation for interviews.",
      "skip": "None.",
      "moduleName": "Prompt Engineering, Structured Outputs, Tool Calling"
    },
    {
      "order": 1,
      "title": "FastAPI for AI Projects - Getting Started in 15 Minutes",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=-IaCV5-mlSk",
      "id": "-IaCV5-mlSk",
      "duration": "16m",
      "difficulty": "Easy",
      "why": "AI-specific FastAPI path.",
      "skip": "None.",
      "moduleName": "AI Backend APIs With FastAPI"
    },
    {
      "order": 2,
      "title": "Microservices with FastAPI - Full Course",
      "creator": "freeCodeCamp.org",
      "url": "https://www.youtube.com/watch?v=Cy9fAvsXGZA",
      "id": "Cy9fAvsXGZA",
      "duration": "1h 29m",
      "difficulty": "Medium",
      "why": "Production API structure.",
      "skip": "Skip basics you know.",
      "moduleName": "AI Backend APIs With FastAPI"
    },
    {
      "order": 3,
      "title": "Event-Driven Architecture with React and FastAPI",
      "creator": "freeCodeCamp.org",
      "url": "https://www.youtube.com/watch?v=NVvIpqmf_Xc",
      "id": "NVvIpqmf_Xc",
      "duration": "1h 38m",
      "difficulty": "Medium",
      "why": "Useful for async workflows.",
      "skip": "UI portions optional.",
      "moduleName": "AI Backend APIs With FastAPI"
    },
    {
      "order": 4,
      "title": "FastAPI Beyond CRUD Full Course",
      "creator": "Ssali Jonathan",
      "url": "https://www.youtube.com/watch?v=TO4aQ3ghFOc",
      "id": "TO4aQ3ghFOc",
      "duration": "12h 53m",
      "difficulty": "Advanced",
      "why": "Deep reference for auth/workers/testing.",
      "skip": "Use as reference, not full watch.",
      "moduleName": "AI Backend APIs With FastAPI"
    },
    {
      "order": 5,
      "title": "How To Build an API with Python (LLM Integration...)",
      "creator": "Tech With Tim",
      "url": "https://www.youtube.com/watch?v=cy6EAp4iNN4",
      "id": "cy6EAp4iNN4",
      "duration": "21m",
      "difficulty": "Easy",
      "why": "AI endpoint implementation.",
      "skip": "Local-only parts optional.",
      "moduleName": "AI Backend APIs With FastAPI"
    },
    {
      "order": 1,
      "title": "Secure FastAPI API with JWT (OAuth2)",
      "creator": "Code with Josh",
      "url": "https://www.youtube.com/watch?v=KxR3OONvDvo",
      "id": "KxR3OONvDvo",
      "duration": "47m",
      "difficulty": "Medium",
      "why": "Practical FastAPI auth.",
      "skip": "None.",
      "moduleName": "Auth, Streaming And Product API Contracts"
    },
    {
      "order": 2,
      "title": "FastAPI JWT Tutorial",
      "creator": "Eric Roby",
      "url": "https://www.youtube.com/watch?v=0A_GCXBCNUQ",
      "id": "0A_GCXBCNUQ",
      "duration": "20m",
      "difficulty": "Medium",
      "why": "Shorter auth implementation.",
      "skip": "Watch as reinforcement.",
      "moduleName": "Auth, Streaming And Product API Contracts"
    },
    {
      "order": 3,
      "title": "API Authentication: JWT, OAuth2, and More",
      "creator": "ByteMonk",
      "url": "https://www.youtube.com/watch?v=xJA8tP74KD0",
      "id": "xJA8tP74KD0",
      "duration": "6m",
      "difficulty": "Easy",
      "why": "Conceptual auth comparison.",
      "skip": "None.",
      "moduleName": "Auth, Streaming And Product API Contracts"
    },
    {
      "order": 4,
      "title": "DataStreaming with LangChain & FastAPI",
      "creator": "Coding Crash Courses",
      "url": "https://www.youtube.com/watch?v=Gn54EbU9mRg",
      "id": "Gn54EbU9mRg",
      "duration": "9m",
      "difficulty": "Medium",
      "why": "Compact direct implementation for streaming LLM responses through a FastAPI backend.",
      "skip": "None.",
      "moduleName": "Auth, Streaming And Product API Contracts"
    },
    {
      "order": 5,
      "title": "Real-Time Agent Applications with WebSockets & FastAPI",
      "creator": "The Neural Maze",
      "url": "https://www.youtube.com/watch?v=svABzOASrzg",
      "id": "svABzOASrzg",
      "duration": "18m",
      "difficulty": "Medium",
      "why": "Agent streaming pattern.",
      "skip": "None.",
      "moduleName": "Auth, Streaming And Product API Contracts"
    },
    {
      "order": 1,
      "title": "PostgreSQL in 100 Seconds",
      "creator": "Fireship",
      "url": "https://www.youtube.com/watch?v=n2Fluyr3lbc",
      "id": "n2Fluyr3lbc",
      "duration": "3m",
      "difficulty": "Easy",
      "why": "Quick refresh.",
      "skip": "None.",
      "moduleName": "Enterprise Data Layer: PostgreSQL And Redis"
    },
    {
      "order": 2,
      "title": "Redis in 100 Seconds",
      "creator": "Fireship",
      "url": "https://www.youtube.com/watch?v=G1rOthIU-uo",
      "id": "G1rOthIU-uo",
      "duration": "2m",
      "difficulty": "Easy",
      "why": "Quick cache mental model.",
      "skip": "None.",
      "moduleName": "Enterprise Data Layer: PostgreSQL And Redis"
    },
    {
      "order": 3,
      "title": "Redis Tutorial In 16 Minutes",
      "creator": "Eric Roby",
      "url": "https://www.youtube.com/watch?v=6nY-kci1rlo",
      "id": "6nY-kci1rlo",
      "duration": "16m",
      "difficulty": "Easy",
      "why": "FastAPI+Redis implementation.",
      "skip": "None.",
      "moduleName": "Enterprise Data Layer: PostgreSQL And Redis"
    },
    {
      "order": 4,
      "title": "Professional Task Queues in Python with Celery, RabbitMQ & Redis",
      "creator": "NeuralNine",
      "url": "https://www.youtube.com/watch?v=0gtdUkEzzn4",
      "id": "0gtdUkEzzn4",
      "duration": "27m",
      "difficulty": "Medium",
      "why": "Useful queue architecture intro.",
      "skip": "RabbitMQ optional.",
      "moduleName": "Enterprise Data Layer: PostgreSQL And Redis"
    },
    {
      "order": 1,
      "title": "Vector Databases simply explained! (Embeddings & Indexes)",
      "creator": "AssemblyAI",
      "url": "https://www.youtube.com/watch?v=dN0lsF2cvm4",
      "id": "dN0lsF2cvm4",
      "duration": "4m",
      "difficulty": "Easy",
      "why": "Best quick concept primer.",
      "skip": "None.",
      "moduleName": "Embeddings And Semantic Search Fundamentals"
    },
    {
      "order": 2,
      "title": "How does a Vector Database work?",
      "creator": "KodeKloud",
      "url": "https://www.youtube.com/watch?v=VVNYQKDLY5s",
      "id": "VVNYQKDLY5s",
      "duration": "11m",
      "difficulty": "Easy",
      "why": "Explains indexing and similarity cleanly.",
      "skip": "None.",
      "moduleName": "Embeddings And Semantic Search Fundamentals"
    },
    {
      "order": 3,
      "title": "Gemini Embedding 2 - Audio, Text, Images, Docs, Videos",
      "creator": "Sam Witteveen",
      "url": "https://www.youtube.com/watch?v=zUkKvWBJ_0I",
      "id": "zUkKvWBJ_0I",
      "duration": "21m",
      "difficulty": "Medium",
      "why": "Up-to-date multimodal embedding context.",
      "skip": "Skip modality demos you do not need.",
      "moduleName": "Embeddings And Semantic Search Fundamentals"
    },
    {
      "order": 4,
      "title": "Cohere AI's LLM for Semantic Search in Python",
      "creator": "James Briggs",
      "url": "https://www.youtube.com/watch?v=ejpc-nbKY2Y",
      "id": "ejpc-nbKY2Y",
      "duration": "15m",
      "difficulty": "Medium",
      "why": "Strong semantic search coding pattern.",
      "skip": "Provider-specific parts optional.",
      "moduleName": "Embeddings And Semantic Search Fundamentals"
    },
    {
      "order": 5,
      "title": "Metadata Filtering for Vector Search + Latest Filter Tech",
      "creator": "James Briggs",
      "url": "https://www.youtube.com/watch?v=H_kJDHvu-v8",
      "id": "H_kJDHvu-v8",
      "duration": "34m",
      "difficulty": "Medium",
      "why": "Covers metadata filters, a production retrieval requirement.",
      "skip": "None.",
      "moduleName": "Embeddings And Semantic Search Fundamentals"
    },
    {
      "order": 1,
      "title": "Getting started with Pinecone monthly webinar (November 2025)",
      "creator": "Pinecone",
      "url": "https://www.youtube.com/watch?v=pY_7RSUnotk",
      "id": "pY_7RSUnotk",
      "duration": "42m",
      "difficulty": "Medium",
      "why": "Official, current Pinecone onboarding.",
      "skip": "Skip marketing intro.",
      "moduleName": "Vector Databases: Pinecone, Chroma, Qdrant, Weaviate, FAISS"
    },
    {
      "order": 2,
      "title": "How to Build a Local AI Agent With Python (Ollama, LangChain & RAG)",
      "creator": "Tech With Tim",
      "url": "https://www.youtube.com/watch?v=E4l91XKQSgw",
      "id": "E4l91XKQSgw",
      "duration": "28m",
      "difficulty": "Medium",
      "why": "Practical local Chroma/LangChain pattern.",
      "skip": "Ollama details optional.",
      "moduleName": "Vector Databases: Pinecone, Chroma, Qdrant, Weaviate, FAISS"
    },
    {
      "order": 3,
      "title": "Let's Build a Local RAG System with Ollama & Qdrant",
      "creator": "Maximilian Schwarzmuller Extended",
      "url": "https://www.youtube.com/watch?v=6diVTn3J7QE",
      "id": "6diVTn3J7QE",
      "duration": "2h 1m",
      "difficulty": "Medium",
      "why": "Solid Qdrant hands-on.",
      "skip": "Skip Ollama if using cloud LLMs.",
      "moduleName": "Vector Databases: Pinecone, Chroma, Qdrant, Weaviate, FAISS"
    },
    {
      "order": 4,
      "title": "How to Build a RAG App with LangChain, Llama 3.1, and ChromaDB",
      "creator": "Data Engineer Academy",
      "url": "https://www.youtube.com/watch?v=Bq6uhc27sPY",
      "id": "Bq6uhc27sPY",
      "duration": "1h 7m",
      "difficulty": "Medium",
      "why": "Practical Chroma app implementation.",
      "skip": "Local model details optional.",
      "moduleName": "Vector Databases: Pinecone, Chroma, Qdrant, Weaviate, FAISS"
    },
    {
      "order": 5,
      "title": "FAISS Vector Library with LangChain and OpenAI (Semantic Search)",
      "creator": "Ryan & Matt Data Science",
      "url": "https://www.youtube.com/watch?v=ZCSsIkyCZk4",
      "id": "ZCSsIkyCZk4",
      "duration": "20m",
      "difficulty": "Medium",
      "why": "Good FAISS semantic search intro.",
      "skip": "None.",
      "moduleName": "Vector Databases: Pinecone, Chroma, Qdrant, Weaviate, FAISS"
    },
    {
      "order": 1,
      "title": "Learn RAG From Scratch - Python AI Tutorial from a LangChain Engineer",
      "creator": "freeCodeCamp.org",
      "url": "https://www.youtube.com/watch?v=sVcwVQRHIc8",
      "id": "sVcwVQRHIc8",
      "duration": "2h 33m",
      "difficulty": "Medium",
      "why": "Best long-form practical foundation for building a first RAG pipeline before advanced retrieval techniques.",
      "skip": "Skip LangChain basics only if you already completed the framework module.",
      "moduleName": "RAG Fundamentals"
    },
    {
      "order": 2,
      "title": "RAG Explained in 12 Minutes",
      "creator": "Aishwarya Srinivasan",
      "url": "https://www.youtube.com/watch?v=v0ynfDPpe4E",
      "id": "v0ynfDPpe4E",
      "duration": "12m",
      "difficulty": "Easy",
      "why": "Crisp mental model.",
      "skip": "None.",
      "moduleName": "RAG Fundamentals"
    },
    {
      "order": 3,
      "title": "RAG with Mistral AI!",
      "creator": "James Briggs",
      "url": "https://www.youtube.com/watch?v=I0c405L7-9A",
      "id": "I0c405L7-9A",
      "duration": "12m",
      "difficulty": "Medium",
      "why": "Shows provider-independent RAG pattern.",
      "skip": "Provider setup optional.",
      "moduleName": "RAG Fundamentals"
    },
    {
      "order": 4,
      "title": "Gemini RAG - File Search Tool",
      "creator": "Sam Witteveen",
      "url": "https://www.youtube.com/watch?v=MuP9ki6Bdtg",
      "id": "MuP9ki6Bdtg",
      "duration": "25m",
      "difficulty": "Medium",
      "why": "Covers Gemini-native retrieval option.",
      "skip": "Skip if not using Gemini yet.",
      "moduleName": "RAG Fundamentals"
    },
    {
      "order": 5,
      "title": "How to Get Your Data Ready for AI Agents (Docs, PDFs, Websites)",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=9lBTS5dM27c",
      "id": "9lBTS5dM27c",
      "duration": "25m",
      "difficulty": "Medium",
      "why": "Great production ingestion mindset.",
      "skip": "None.",
      "moduleName": "RAG Fundamentals"
    },
    {
      "order": 1,
      "title": "Advanced RAG 03 - Hybrid Search BM25 & Ensembles",
      "creator": "Sam Witteveen",
      "url": "https://www.youtube.com/watch?v=lYxGYXjfrNI",
      "id": "lYxGYXjfrNI",
      "duration": "7m",
      "difficulty": "Medium",
      "why": "Compact hybrid search explanation.",
      "skip": "None.",
      "moduleName": "Advanced RAG, Hybrid Search And Re-ranking"
    },
    {
      "order": 2,
      "title": "The Complete Guide to Hybrid Search in RAG (BM25 + Embeddings + Reranker)",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=XvKiTfd6Xvo",
      "id": "XvKiTfd6Xvo",
      "duration": "59m",
      "difficulty": "Advanced",
      "why": "Best project-quality explanation of dense plus keyword search and reranking in one workflow.",
      "skip": "None.",
      "moduleName": "Advanced RAG, Hybrid Search And Re-ranking"
    },
    {
      "order": 3,
      "title": "RAG But Better: Rerankers with Cohere AI",
      "creator": "James Briggs",
      "url": "https://www.youtube.com/watch?v=Uh9bYiVrW_s",
      "id": "Uh9bYiVrW_s",
      "duration": "24m",
      "difficulty": "Medium",
      "why": "Practical reranking implementation.",
      "skip": "Cohere specifics optional.",
      "moduleName": "Advanced RAG, Hybrid Search And Re-ranking"
    },
    {
      "order": 4,
      "title": "LangChain Multi-Query Retriever for RAG",
      "creator": "James Briggs",
      "url": "https://www.youtube.com/watch?v=VFf8XJUIHnU",
      "id": "VFf8XJUIHnU",
      "duration": "19m",
      "difficulty": "Medium",
      "why": "Production-relevant query expansion.",
      "skip": "None.",
      "moduleName": "Advanced RAG, Hybrid Search And Re-ranking"
    },
    {
      "order": 5,
      "title": "Advanced RAG techniques for developers",
      "creator": "Google Cloud Tech",
      "url": "https://www.youtube.com/watch?v=sGvXO7CVwc0",
      "id": "sGvXO7CVwc0",
      "duration": "8m",
      "difficulty": "Medium",
      "why": "Cloud/enterprise advanced RAG overview.",
      "skip": "None.",
      "moduleName": "Advanced RAG, Hybrid Search And Re-ranking"
    },
    {
      "order": 1,
      "title": "LangChain vs LangGraph vs LangSmith",
      "creator": "codebasics",
      "url": "https://www.youtube.com/watch?v=vJOGC8QJZJQ",
      "id": "vJOGC8QJZJQ",
      "duration": "10m",
      "difficulty": "Easy",
      "why": "Clarifies ecosystem roles.",
      "skip": "None.",
      "moduleName": "LangChain And LlamaIndex"
    },
    {
      "order": 2,
      "title": "Agentic AI Crash Course using LangChain",
      "creator": "codebasics",
      "url": "https://www.youtube.com/watch?v=D74el9mvNak",
      "id": "D74el9mvNak",
      "duration": "2h 24m",
      "difficulty": "Medium",
      "why": "Practical LangChain walkthrough.",
      "skip": "Skip repeated setup.",
      "moduleName": "LangChain And LlamaIndex"
    },
    {
      "order": 3,
      "title": "Introduction to LlamaIndex with Python (2025)",
      "creator": "Alejandro AO",
      "url": "https://www.youtube.com/watch?v=cCyYGYyCka4",
      "id": "cCyYGYyCka4",
      "duration": "40m",
      "difficulty": "Medium",
      "why": "Current LlamaIndex onboarding.",
      "skip": "None.",
      "moduleName": "LangChain And LlamaIndex"
    },
    {
      "order": 4,
      "title": "End to end RAG LLM App Using Llamaindex and OpenAI",
      "creator": "Krish Naik",
      "url": "https://www.youtube.com/watch?v=hH4WkgILUD4",
      "id": "hH4WkgILUD4",
      "duration": "27m",
      "difficulty": "Medium",
      "why": "Fast LlamaIndex RAG path.",
      "skip": "Skip Streamlit polish.",
      "moduleName": "LangChain And LlamaIndex"
    },
    {
      "order": 5,
      "title": "Agentic Document Processing with LlamaCloud",
      "creator": "LlamaIndex",
      "url": "https://www.youtube.com/watch?v=6q0jMcdbijQ",
      "id": "6q0jMcdbijQ",
      "duration": "53m",
      "difficulty": "Advanced",
      "why": "Official advanced document processing context.",
      "skip": "Skip product-specific pricing.",
      "moduleName": "LangChain And LlamaIndex"
    },
    {
      "order": 1,
      "title": "Building Effective Agents with LangGraph",
      "creator": "LangChain",
      "url": "https://www.youtube.com/watch?v=aHCDrAbH_go",
      "id": "aHCDrAbH_go",
      "duration": "32m",
      "difficulty": "Medium",
      "why": "Official practical agent architecture.",
      "skip": "None.",
      "moduleName": "LangGraph And Model Context Protocol (MCP)"
    },
    {
      "order": 2,
      "title": "LangGraph Complete Course for Beginners - Complex AI Agents with Python",
      "creator": "freeCodeCamp.org",
      "url": "https://www.youtube.com/watch?v=jGg_1h0qzaM",
      "id": "jGg_1h0qzaM",
      "duration": "3h 10m",
      "difficulty": "Medium",
      "why": "Deep hands-on graph course.",
      "skip": "Watch selected chapters as needed.",
      "moduleName": "LangGraph And Model Context Protocol (MCP)"
    },
    {
      "order": 3,
      "title": "Model Context Protocol Clearly Explained",
      "creator": "codebasics",
      "url": "https://www.youtube.com/watch?v=tzrwxLNHtRY",
      "id": "tzrwxLNHtRY",
      "duration": "15m",
      "difficulty": "Easy",
      "why": "Clear MCP concept primer.",
      "skip": "None.",
      "moduleName": "LangGraph And Model Context Protocol (MCP)"
    },
    {
      "order": 4,
      "title": "Intro to MCP Servers - Model Context Protocol with Python Course",
      "creator": "freeCodeCamp.org",
      "url": "https://www.youtube.com/watch?v=DosHnyq78xY",
      "id": "DosHnyq78xY",
      "duration": "1h 13m",
      "difficulty": "Medium",
      "why": "Best Python MCP build path.",
      "skip": "None.",
      "moduleName": "LangGraph And Model Context Protocol (MCP)"
    },
    {
      "order": 5,
      "title": "MCP Crash Course: What Python Developers Need to Know",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=5xqFjh56AwM",
      "id": "5xqFjh56AwM",
      "duration": "58m",
      "difficulty": "Medium",
      "why": "Python-developer friendly MCP perspective.",
      "skip": "None.",
      "moduleName": "LangGraph And Model Context Protocol (MCP)"
    },
    {
      "order": 1,
      "title": "Building AI Agents in Pure Python - Beginner Course",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=bZzyPscbtI8",
      "id": "bZzyPscbtI8",
      "duration": "47m",
      "difficulty": "Medium",
      "why": "Shows agent mechanics without framework magic.",
      "skip": "None.",
      "moduleName": "AI Agents, CrewAI, AG2/AutoGen And n8n Workflows"
    },
    {
      "order": 2,
      "title": "CrewAI Tutorial - Agentic AI Tutorial",
      "creator": "codebasics",
      "url": "https://www.youtube.com/watch?v=G42J2MSKyc8",
      "id": "G42J2MSKyc8",
      "duration": "1h 11m",
      "difficulty": "Medium",
      "why": "Best practical CrewAI intro from metadata pass.",
      "skip": "None.",
      "moduleName": "AI Agents, CrewAI, AG2/AutoGen And n8n Workflows"
    },
    {
      "order": 3,
      "title": "AutoGen Crash Course For Beginners",
      "creator": "Krish Naik",
      "url": "https://www.youtube.com/watch?v=R8KQ5nwpXl8",
      "id": "R8KQ5nwpXl8",
      "duration": "48m",
      "difficulty": "Medium",
      "why": "Better-known practical educator for AutoGen/AG2-style multi-agent patterns than random short demos.",
      "skip": "Watch for concepts; use LangGraph for most production stateful agents.",
      "moduleName": "AI Agents, CrewAI, AG2/AutoGen And n8n Workflows"
    },
    {
      "order": 4,
      "title": "n8n Quick Start Tutorial: Build Your First AI Agent [2026]",
      "creator": "n8n and Flowgrammer",
      "url": "https://www.youtube.com/watch?v=GuaKeDS6UKU",
      "id": "GuaKeDS6UKU",
      "duration": "21m",
      "difficulty": "Easy",
      "why": "Current n8n AI workflow starter.",
      "skip": "None.",
      "moduleName": "AI Agents, CrewAI, AG2/AutoGen And n8n Workflows"
    },
    {
      "order": 1,
      "title": "Background Tasks with FastAPI Background Tasks and Celery + Redis",
      "creator": "Ssali Jonathan",
      "url": "https://www.youtube.com/watch?v=eAHAKowv6hk",
      "id": "eAHAKowv6hk",
      "duration": "45m",
      "difficulty": "Medium",
      "why": "Direct FastAPI worker implementation.",
      "skip": "None.",
      "moduleName": "Background Workers And Evaluation"
    },
    {
      "order": 2,
      "title": "Getting Started With Celery: Asynchronous Tasks in Python",
      "creator": "Pretty Printed",
      "url": "https://www.youtube.com/watch?v=VRHVEporra0",
      "id": "VRHVEporra0",
      "duration": "12m",
      "difficulty": "Easy",
      "why": "Quick Celery mental model.",
      "skip": "None.",
      "moduleName": "Background Workers And Evaluation"
    },
    {
      "order": 3,
      "title": "How to Systematically Setup LLM Evals",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=a3SMraZWNNs",
      "id": "a3SMraZWNNs",
      "duration": "55m",
      "difficulty": "Advanced",
      "why": "Best practical eval design video in this list.",
      "skip": "None.",
      "moduleName": "Background Workers And Evaluation"
    },
    {
      "order": 4,
      "title": "The 100% EASIEST Way to Test LLMs & AI Agents",
      "creator": "Execute Automation",
      "url": "https://www.youtube.com/watch?v=uz5BEadZwLc",
      "id": "uz5BEadZwLc",
      "duration": "19m",
      "difficulty": "Medium",
      "why": "Reinforces practical agent/LLM testing.",
      "skip": "None.",
      "moduleName": "Background Workers And Evaluation"
    },
    {
      "order": 5,
      "title": "DeepEval for RAG: Let's Test If Your LLM Really Works",
      "creator": "Execute Automation",
      "url": "https://www.youtube.com/watch?v=3g5CbfXsm_8",
      "id": "3g5CbfXsm_8",
      "duration": "20m",
      "difficulty": "Medium",
      "why": "Practical DeepEval RAG tests.",
      "skip": "None.",
      "moduleName": "Background Workers And Evaluation"
    },
    {
      "order": 1,
      "title": "What Is LangSmith? Explained in 5 Minutes",
      "creator": "LangChain",
      "url": "https://www.youtube.com/watch?v=kYtnLaJeia8",
      "id": "kYtnLaJeia8",
      "duration": "5m",
      "difficulty": "Easy",
      "why": "Official quick mental model.",
      "skip": "None.",
      "moduleName": "LLMOps Observability: LangSmith, Langfuse, OpenTelemetry And APM"
    },
    {
      "order": 2,
      "title": "LangSmith Tutorial - LLM Evaluation for Beginners",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=tFXm5ijih98",
      "id": "tFXm5ijih98",
      "duration": "36m",
      "difficulty": "Medium",
      "why": "Hands-on LangSmith eval workflow.",
      "skip": "None.",
      "moduleName": "LLMOps Observability: LangSmith, Langfuse, OpenTelemetry And APM"
    },
    {
      "order": 3,
      "title": "10 min Walkthrough of Langfuse",
      "creator": "Langfuse",
      "url": "https://www.youtube.com/watch?v=2E8iTvGo9Hs",
      "id": "2E8iTvGo9Hs",
      "duration": "10m",
      "difficulty": "Easy",
      "why": "Official Langfuse orientation.",
      "skip": "None.",
      "moduleName": "LLMOps Observability: LangSmith, Langfuse, OpenTelemetry And APM"
    },
    {
      "order": 4,
      "title": "Get Started with Langfuse - Open-Source LLM Monitoring",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=epnPfe5am3I",
      "id": "epnPfe5am3I",
      "duration": "12m",
      "difficulty": "Medium",
      "why": "Practical open-source tracing setup.",
      "skip": "None.",
      "moduleName": "LLMOps Observability: LangSmith, Langfuse, OpenTelemetry And APM"
    },
    {
      "order": 5,
      "title": "Intro to OpenTelemetry and LLM Observability",
      "creator": "Arize AI",
      "url": "https://www.youtube.com/watch?v=0I0ZrmyoTpM",
      "id": "0I0ZrmyoTpM",
      "duration": "16m",
      "difficulty": "Medium",
      "why": "Connects AI traces to broader observability.",
      "skip": "None.",
      "moduleName": "LLMOps Observability: LangSmith, Langfuse, OpenTelemetry And APM"
    },
    {
      "order": 1,
      "title": "NVIDIA NeMo Guardrails: Full Walkthrough for Chatbots / AI",
      "creator": "James Briggs",
      "url": "https://www.youtube.com/watch?v=SwqusllMCnE",
      "id": "SwqusllMCnE",
      "duration": "21m",
      "difficulty": "Medium",
      "why": "Practical guardrails implementation.",
      "skip": "None.",
      "moduleName": "Guardrails And Production Safety"
    },
    {
      "order": 2,
      "title": "Guardrails for LLM Applications",
      "creator": "Sunny Savita",
      "url": "https://www.youtube.com/watch?v=7V1w5gnZ-kw",
      "id": "7V1w5gnZ-kw",
      "duration": "1h 26m",
      "difficulty": "Medium",
      "why": "Full Guardrails AI walkthrough.",
      "skip": "Skip repeated basics.",
      "moduleName": "Guardrails And Production Safety"
    },
    {
      "order": 3,
      "title": "LLM Observability with OpenTelemetry - Ultimate Guide",
      "creator": "Agenta AI",
      "url": "https://www.youtube.com/watch?v=crEyMDJ4Bp0",
      "id": "crEyMDJ4Bp0",
      "duration": "9m",
      "difficulty": "Medium",
      "why": "OTel-focused LLM observability.",
      "skip": "None.",
      "moduleName": "Guardrails And Production Safety"
    },
    {
      "order": 4,
      "title": "Building Production-Ready RAG Systems",
      "creator": "Conf42",
      "url": "https://www.youtube.com/watch?v=ASBY-UrPFv8",
      "id": "ASBY-UrPFv8",
      "duration": "19m",
      "difficulty": "Medium",
      "why": "Production architecture concerns.",
      "skip": "None.",
      "moduleName": "Guardrails And Production Safety"
    },
    {
      "order": 5,
      "title": "Building Production RAG Systems: Architecture, Scaling & Cost Optimization",
      "creator": "Mukul Raina",
      "url": "https://www.youtube.com/watch?v=uZ56v9xfcBw",
      "id": "uZ56v9xfcBw",
      "duration": "1h 5m",
      "difficulty": "Advanced",
      "why": "Architecture, scaling, cost in one video.",
      "skip": "None.",
      "moduleName": "Guardrails And Production Safety"
    },
    {
      "order": 1,
      "title": "Docker Crash Course for Absolute Beginners [NEW]",
      "creator": "TechWorld with Nana",
      "url": "https://www.youtube.com/watch?v=pg19Z8LL06w",
      "id": "pg19Z8LL06w",
      "duration": "1h 8m",
      "difficulty": "Easy",
      "why": "Modern Docker primer.",
      "skip": "Skip basics if comfortable.",
      "moduleName": "Docker, Kubernetes And CI/CD For AI Apps"
    },
    {
      "order": 2,
      "title": "Docker Tutorial for Beginners [FULL COURSE in 3 Hours]",
      "creator": "TechWorld with Nana",
      "url": "https://www.youtube.com/watch?v=3c-iBn73dDE",
      "id": "3c-iBn73dDE",
      "duration": "2h 46m",
      "difficulty": "Medium",
      "why": "Complete Docker reference.",
      "skip": "Watch build/deploy chapters.",
      "moduleName": "Docker, Kubernetes And CI/CD For AI Apps"
    },
    {
      "order": 3,
      "title": "What is Kubernetes? Kubernetes Explained in 15 mins",
      "creator": "TechWorld with Nana",
      "url": "https://www.youtube.com/watch?v=VnvRFRk_51k",
      "id": "VnvRFRk_51k",
      "duration": "14m",
      "difficulty": "Easy",
      "why": "Clear architecture intro from the best practical Kubernetes educator for application developers.",
      "skip": "None.",
      "moduleName": "Docker, Kubernetes And CI/CD For AI Apps"
    },
    {
      "order": 4,
      "title": "Kubernetes Crash Course for Absolute Beginners [NEW]",
      "creator": "TechWorld with Nana",
      "url": "https://www.youtube.com/watch?v=s_o8dwzRlu4",
      "id": "s_o8dwzRlu4",
      "duration": "1h 12m",
      "difficulty": "Medium",
      "why": "Practical K8s path.",
      "skip": "None.",
      "moduleName": "Docker, Kubernetes And CI/CD For AI Apps"
    },
    {
      "order": 5,
      "title": "Kubernetes Tutorial for Beginners [FULL COURSE in 4 Hours]",
      "creator": "TechWorld with Nana",
      "url": "https://www.youtube.com/watch?v=X48VuDVv0do",
      "id": "X48VuDVv0do",
      "duration": "3h 37m",
      "difficulty": "Medium",
      "why": "Full reference.",
      "skip": "Use selectively.",
      "moduleName": "Docker, Kubernetes And CI/CD For AI Apps"
    },
    {
      "order": 1,
      "title": "Introducing Azure AI Foundry",
      "creator": "Microsoft Mechanics",
      "url": "https://www.youtube.com/watch?v=GD7MnIwAxYM",
      "id": "GD7MnIwAxYM",
      "duration": "13m",
      "difficulty": "Easy",
      "why": "Official Azure AI platform intro.",
      "skip": "None.",
      "moduleName": "Enterprise Cloud AI: AWS Bedrock, Azure AI And Vertex AI"
    },
    {
      "order": 2,
      "title": "Azure AI Foundry Overview",
      "creator": "John Savill's Technical Training",
      "url": "https://www.youtube.com/watch?v=Sq8Cq7RZM2o",
      "id": "Sq8Cq7RZM2o",
      "duration": "1h 28m",
      "difficulty": "Medium",
      "why": "Deep, enterprise-oriented Azure explanation.",
      "skip": "Skip service catalog you do not use.",
      "moduleName": "Enterprise Cloud AI: AWS Bedrock, Azure AI And Vertex AI"
    },
    {
      "order": 3,
      "title": "Amazon Bedrock for Beginners - From First Prompt to AI Agent",
      "creator": "AWS Developers and Morgan Willis",
      "url": "https://www.youtube.com/watch?v=FAgmR9VV0GQ",
      "id": "FAgmR9VV0GQ",
      "duration": "45m",
      "difficulty": "Medium",
      "why": "Official/practical Bedrock path.",
      "skip": "None.",
      "moduleName": "Enterprise Cloud AI: AWS Bedrock, Azure AI And Vertex AI"
    },
    {
      "order": 4,
      "title": "Introduction to Gemini on Vertex AI",
      "creator": "Google Cloud Tech",
      "url": "https://www.youtube.com/watch?v=YfiLUpNejpE",
      "id": "YfiLUpNejpE",
      "duration": "5m",
      "difficulty": "Easy",
      "why": "Official Vertex/Gemini intro.",
      "skip": "None.",
      "moduleName": "Enterprise Cloud AI: AWS Bedrock, Azure AI And Vertex AI"
    },
    {
      "order": 5,
      "title": "Run Google's Models on Vertex AI with Python + EU Data Residency Tips",
      "creator": "NeuralNine",
      "url": "https://www.youtube.com/watch?v=7HkCWwQhLWs",
      "id": "7HkCWwQhLWs",
      "duration": "10m",
      "difficulty": "Medium",
      "why": "Practical Python and compliance angle.",
      "skip": "Region details optional.",
      "moduleName": "Enterprise Cloud AI: AWS Bedrock, Azure AI And Vertex AI"
    },
    {
      "order": 1,
      "title": "OWASP's Top 10 Ways to Attack LLMs",
      "creator": "IBM Technology",
      "url": "https://www.youtube.com/watch?v=gUNXZMcd2jU",
      "id": "gUNXZMcd2jU",
      "duration": "25m",
      "difficulty": "Medium",
      "why": "Security risks from a credible enterprise technology source.",
      "skip": "None.",
      "moduleName": "Scaling, Cost Optimization, Security And Enterprise AI Architecture"
    },
    {
      "order": 2,
      "title": "Explained: The OWASP Top 10 for Large Language Model Applications",
      "creator": "IBM Technology",
      "url": "https://www.youtube.com/watch?v=cYuesqIKf9A",
      "id": "cYuesqIKf9A",
      "duration": "14m",
      "difficulty": "Medium",
      "why": "Concise risk taxonomy for interviews and architecture reviews.",
      "skip": "If watched the previous IBM video, skim for terminology.",
      "moduleName": "Scaling, Cost Optimization, Security And Enterprise AI Architecture"
    },
    {
      "order": 3,
      "title": "What Is a Prompt Injection Attack?",
      "creator": "IBM Technology",
      "url": "https://www.youtube.com/watch?v=jrHRe9lSqqA",
      "id": "jrHRe9lSqqA",
      "duration": "11m",
      "difficulty": "Easy",
      "why": "Clear threat primer for enterprise AI security.",
      "skip": "None.",
      "moduleName": "Scaling, Cost Optimization, Security And Enterprise AI Architecture"
    },
    {
      "order": 4,
      "title": "Beyond Simple RAG: Quality, Scale and Cost-Efficient Retrieval",
      "creator": "Databricks",
      "url": "https://www.youtube.com/watch?v=pUKvTs6Eg4k",
      "id": "pUKvTs6Eg4k",
      "duration": "37m",
      "difficulty": "Advanced",
      "why": "Enterprise-grade retrieval architecture from a credible platform team.",
      "skip": "Vendor-specific details optional.",
      "moduleName": "Scaling, Cost Optimization, Security And Enterprise AI Architecture"
    },
    {
      "order": 1,
      "title": "How to Build a Production-Ready RAG AI Agent in Python",
      "creator": "Tech With Tim",
      "url": "https://www.youtube.com/watch?v=AUQJ9eeP-Ls",
      "id": "AUQJ9eeP-Ls",
      "duration": "1h 16m",
      "difficulty": "Advanced",
      "why": "End-to-end production-flavored build.",
      "skip": "Local model details optional.",
      "moduleName": "End-To-End Capstone Project"
    },
    {
      "order": 2,
      "title": "How AI Agents Actually Work (Explained in One Python File)",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=Q3Gb7Rjre3U",
      "id": "Q3Gb7Rjre3U",
      "duration": "32m",
      "difficulty": "Medium",
      "why": "Final agent mental model reset.",
      "skip": "None.",
      "moduleName": "End-To-End Capstone Project"
    },
    {
      "order": 3,
      "title": "How to Build Human-in-the-Loop for AI Agents",
      "creator": "Dave Ebbelaar",
      "url": "https://www.youtube.com/watch?v=7GOxUgVTz3s",
      "id": "7GOxUgVTz3s",
      "duration": "25m",
      "difficulty": "Advanced",
      "why": "Enterprise-grade approval pattern.",
      "skip": "None.",
      "moduleName": "End-To-End Capstone Project"
    },
    {
      "order": 4,
      "title": "Building AI agents with Structured Outputs, Function Calling, and MCP",
      "creator": "Devoxx",
      "url": "https://www.youtube.com/watch?v=HmneQx1maCI",
      "id": "HmneQx1maCI",
      "duration": "49m",
      "difficulty": "Advanced",
      "why": "Ties together agent contracts, tools, and MCP.",
      "skip": "Conference intro optional.",
      "moduleName": "End-To-End Capstone Project"
    }
  ],
  "invalidDesktopYoutubeCount": 26,
  "scalingTopics": [
    {
      "title": "KV-cache and vLLM/PagedAttention",
      "why": "Needed to understand inference throughput, memory pressure, batching, and why self-hosted models scale differently from API calls."
    },
    {
      "title": "Semantic caching and token budgeting",
      "why": "Prevents runaway bills and speeds up repeated enterprise queries."
    },
    {
      "title": "Model routing and cascading",
      "why": "Routes simple tasks to cheaper models and escalates only complex work."
    },
    {
      "title": "Token-flood DoS protection",
      "why": "LLM apps need user/tenant budget limits, max context limits, and rate limits."
    },
    {
      "title": "Private endpoints, IAM, VPC, managed identity",
      "why": "Enterprise AI often requires Azure OpenAI, Bedrock, or Vertex behind cloud security boundaries."
    },
    {
      "title": "Readiness/liveness probes and worker queues",
      "why": "AI services have slow startup, heavy memory usage, and long-running ingestion jobs."
    },
    {
      "title": "Enterprise APM integration: Grafana, Splunk, Dynatrace",
      "why": "FDEs must fit AI services into the customer observability stack, not force every enterprise to adopt a separate AI-only dashboard."
    },
    {
      "title": "SLOs, alerts, runbooks and postmortems",
      "why": "FDEs need SRE habits for customer systems: define SLIs/SLOs, route actionable alerts, maintain runbooks, run incident reviews, and hand off support cleanly."
    },
    {
      "title": "Multi-region AWS, DR, RTO/RPO and failover",
      "why": "The brochure explicitly expects FDEs to understand multi-region cloud delivery and disaster recovery, not only single-region deployment."
    },
    {
      "title": "Storage architecture, sharding, CDN, HA and fault tolerance",
      "why": "Customer deployments need data placement, partitioning, CDN strategy, high availability, and failure-mode planning."
    },
    {
      "title": "Multi-tenant SaaS isolation and role-based delivery",
      "why": "Enterprise AI products often serve many tenants, teams, or departments with strict data isolation and permission boundaries."
    },
    {
      "title": "AI-first frontends and customer demo surfaces",
      "why": "FDEs need to turn backend AI capability into usable demos, workflows, and adoption-ready product surfaces."
    },
    {
      "title": "Production-grade integration harnesses and contract tests",
      "why": "FDEs often integrate with customer APIs they do not own, so contract tests, smoke tests, mocked dependencies, and repeatable harnesses are required."
    },
    {
      "title": "Live production debugging under pressure",
      "why": "The lab expects FDEs to debug systems using logs, metrics, traces, feature flags, rollback plans, and customer-safe incident practices."
    },
    {
      "title": "CDC, webhooks, reverse ETL and cloud-native data quality gates",
      "why": "Enterprise integrations require data freshness patterns beyond batch ingestion: CDC, webhooks, reverse ETL, validation, retries, and quarantine queues."
    },
    {
      "title": "MLOps drift, decay and managed model monitoring",
      "why": "Even application-focused AI engineers need to know when a model or embedding pipeline is degrading after launch."
    },
    {
      "title": "Fine-tuning strategy for client use cases",
      "why": "Fine-tuning should be covered as a decision framework and implementation option, even if the main curriculum avoids model training theory."
    },
    {
      "title": "Cross-account access, attack surfaces, compliance and hardening",
      "why": "FDEs frequently deploy into customer cloud/account boundaries and must reason about IAM trust, secrets, privacy, audit, and attack paths."
    },
    {
      "title": "Performance engineering and load testing systems you did not build",
      "why": "Customer systems are inherited, messy, and already live; FDEs need load tests, latency budgets, and bottleneck diagnosis."
    }
  ],
  "fdeVideos": [
    {
      "title": "Forward Deployed Engineer: The Hottest AI Job of 2026",
      "creator": "Aishwarya Srinivasan and The Gen Academy",
      "url": "https://www.youtube.com/watch?v=w-Z4QYK1QL4",
      "duration": "15m",
      "why": "Best modern overview of the AI-era FDE role and why it differs from ordinary backend engineering."
    },
    {
      "title": "Forward Deployed Engineer | Role & Interview Overview",
      "creator": "Exponent",
      "url": "https://www.youtube.com/watch?v=qZgErBZk0GY",
      "duration": "22m",
      "why": "Useful for interview expectations and role framing."
    },
    {
      "title": "The Role of a Forward Deployed Software Engineer",
      "creator": "Palantir",
      "url": "https://www.youtube.com/watch?v=5OYy_UtINo4",
      "duration": "4m",
      "why": "Original FDE-style explanation from the company most associated with the role."
    },
    {
      "title": "What is Forward Deployed Engineer (FDE) Role?",
      "creator": "Piyush Garg",
      "url": "https://www.youtube.com/watch?v=7JlEs6zyB_U",
      "duration": "18m",
      "why": "Practical explanation from a software-engineering career perspective."
    },
    {
      "title": "The Production AI Playbook: Deploying Agents at Enterprise Scale",
      "creator": "AI Engineer",
      "url": "https://www.youtube.com/watch?v=ObTPqBGsEbA",
      "duration": "37m",
      "why": "Good FDE-adjacent production mindset: quality, cost, observability, rollout, and scale."
    },
    {
      "title": "Production-Ready AI Applications: Enterprise Best Practices & Architecture",
      "creator": "AaiTech",
      "url": "https://www.youtube.com/watch?v=b0k9s72KkvA",
      "duration": "18m",
      "why": "Architecture and deployment practices useful for customer-facing AI delivery."
    }
  ],
  "coverageTags": [
    "AWS Bedrock AgentCore",
    "AWS FDE Cloud Track",
    "Azure AI Foundry",
    "BentoML",
    "Business Case Development",
    "CRM Integrations",
    "Change Management",
    "Optional Claude Agent Teams",
    "Optional Claude Code Agent SDK",
    "Optional Claude Dynamic Workflows",
    "Optional Claude Goals",
    "Optional Claude Hooks",
    "Optional Claude Skills",
    "Clearbit API",
    "Client Discovery",
    "DSPy",
    "FastAPI Streaming Responses (Async Generators)",
    "Guardrails (NeMo)",
    "Helicone",
    "Hybrid Search (BM25+Dense)",
    "LLM Security (OWASP Top 10)",
    "Low-Code Pipelines",
    "Modal",
    "Model Context Protocol (MCP)",
    "PII Detection",
    "PostgreSQL CRM ingestion",
    "ReAct Prompting",
    "Replicate",
    "Semantic Kernel",
    "Slack Webhooks",
    "Stakeholder Communication",
    "Together AI",
    "Vertex AI Agent Engine",
    "Webhook Triggers",
    "Weights & Biases",
    "Zendesk API",
    "n8n AI Workflows",
    "Grafana",
    "Splunk",
    "Dynatrace",
    "Enterprise APM",
    "OpenTelemetry Export",
    "SLO/SLI",
    "Alerting",
    "Runbooks",
    "Incident Response",
    "Postmortems",
    "Support Handoff",
    "AI-first Frontends",
    "React TypeScript AI Apps",
    "Advanced Data Engineering",
    "Airflow RAG Pipelines",
    "Multi-Tenant SaaS",
    "Roles and Permissions",
    "AWS Multi-Region",
    "Disaster Recovery",
    "RTO/RPO",
    "Storage Architecture",
    "Sharding",
    "CDN",
    "High Availability",
    "Fault Tolerance",
    "Human-in-the-Loop",
    "Integration Harness",
    "Contract Testing",
    "Production Debugging",
    "Live Systems",
    "CDC",
    "Webhooks",
    "Reverse ETL",
    "Data Quality Gates",
    "Model Drift",
    "Model Decay",
    "Fine-tuning Strategy",
    "LLM Routing",
    "Prompt Caching",
    "Cross-Account Access",
    "Attack Surfaces",
    "Compliance Frameworks",
    "Data Privacy for ML",
    "Security Audit",
    "Hardening",
    "Load Testing",
    "Performance Engineering"
  ],
  "qualityPolicy": {
    "summary": "Video-first curriculum curated for practical AI Engineering/FDE learning. Weak/random short demos were removed or downgraded in favor of known educators, official vendor channels, conference talks, or respected practitioner channels.",
    "preferredCreators": [
      "Dave Ebbelaar",
      "James Briggs",
      "Sam Witteveen",
      "Cole Medin",
      "Nicholas Renotte",
      "n8n Official",
      "TechWorld with Nana",
      "freeCodeCamp.org",
      "DeepLearning.AI",
      "IBM Technology",
      "Google Cloud Tech",
      "Microsoft Mechanics",
      "AWS Developers",
      "LangChain",
      "LlamaIndex",
      "Langfuse",
      "Pinecone",
      "Databricks",
      "Tech With Tim",
      "codebasics"
    ]
  },
  "claudeFdeTrack": {
    "title": "Optional AI Coding Tooling Track",
    "subtitle": "Optional productivity layer for AI engineers: Claude Code, Skills, MCP, hooks, goals, subagents, and Agent SDK. Useful for delivery speed, but not core FDE curriculum.",
    "coverage": [
      "Optional Claude Skills",
      "Claude Code workflows",
      "Goals",
      "Hooks",
      "Agent teams",
      "Subagents",
      "Claude Agent SDK",
      "MCP",
      "Claude on Bedrock/Vertex/Azure"
    ],
    "videos": [
      {
        "title": "The Ultimate Claude Code Guide | MCP, Skills & More",
        "creator": "Tech With Tim",
        "url": "https://www.youtube.com/watch?v=uogzSxOw4LU",
        "duration": "37m 41s",
        "why": "Best broad practical Claude Code walkthrough covering MCP and Skills from a respected coding educator."
      },
      {
        "title": "Build a proactive agent workflow with Claude Code",
        "creator": "Claude",
        "url": "https://www.youtube.com/watch?v=eSP7PLTXNy8",
        "duration": "22m 4s",
        "why": "Official Claude channel video for proactive workflow thinking, useful for FDE automation design."
      },
      {
        "title": "5 Claude Code skills I use every single day",
        "creator": "Matt Pocock",
        "url": "https://www.youtube.com/watch?v=EJyuu6zlQCg",
        "duration": "16m 42s",
        "why": "Strong senior-engineer view of practical Skills usage, not consumer productivity fluff."
      },
      {
        "title": "The Agent Loop Explained | Claude Agent SDK Series",
        "creator": "Piyush Garg",
        "url": "https://www.youtube.com/watch?v=Cgjixg5wvfw",
        "duration": "10m 44s",
        "why": "Good compact explanation of the agent loop that powers SDK-style automation."
      },
      {
        "title": "You Can Build The Craziest Things with Claudes Agent SDK",
        "creator": "Traversy Media",
        "url": "https://www.youtube.com/watch?v=ChaQ_tZDBFg",
        "duration": "14m 25s",
        "why": "Hands-on Claude Agent SDK build from a widely trusted practical coding channel."
      },
      {
        "title": "Agent Skills or MCP in the era of Claude Code?",
        "creator": "Confluent Developer",
        "url": "https://www.youtube.com/watch?v=pvxNcQTcIy4",
        "duration": "9m 57s",
        "why": "Useful architecture distinction between reusable Skills and external MCP tools."
      }
    ]
  },
  "fdeCloudTrack": {
    "title": "Cloud FDE Track: AWS-First Enterprise AI Deployment",
    "subtitle": "Yes, a cloud module is needed for FDE. AWS gets extra weight because Bedrock, AgentCore, IAM, VPC, Knowledge Bases, observability, and customer deployment patterns map directly to FDE work.",
    "coverage": [
      "AWS Bedrock",
      "AgentCore",
      "Knowledge Bases",
      "Managed RAG",
      "IAM and identity",
      "MCP gateways",
      "Observability",
      "Azure AI Foundry",
      "Vertex AI Agent Engine"
    ],
    "videos": [
      {
        "title": "Deploy ANY AI Agent to Production in Minutes | Amazon Bedrock AgentCore Tutorial",
        "creator": "AWS Developers",
        "url": "https://www.youtube.com/watch?v=N7FGbBq1mI4",
        "duration": "15m 56s",
        "why": "Directly relevant to FDE: deploying and operating agents, not just calling a model."
      },
      {
        "title": "AWS re:Invent 2025 - Amazon Bedrock Agents and AgentCore Design Patterns",
        "creator": "AWS Events",
        "url": "https://www.youtube.com/watch?v=GYlPFmrATjU",
        "duration": "55m 15s",
        "why": "Architecture-level patterns for enterprise Bedrock/AgentCore deployments."
      },
      {
        "title": "Amazon Bedrock AgentCore: Deploy & Operate AI Agents in Minutes",
        "creator": "Amazon Web Services",
        "url": "https://www.youtube.com/watch?v=usFIb9aEd1U",
        "duration": "6m 57s",
        "why": "Official short overview of why AgentCore exists and how it supports production operations."
      },
      {
        "title": "Introduction to Vertex AI Agent Engine",
        "creator": "Google Cloud Tech",
        "url": "https://www.youtube.com/watch?v=NrgoZLcY3Kk",
        "duration": "5m 26s",
        "why": "Official Vertex agent runtime orientation for Google Cloud customers."
      }
    ]
  },
  "fdeAcademyCoverage": [
    {
      "area": "Python for production systems",
      "status": "Covered",
      "where": "Module 1 + FastAPI backend modules"
    },
    {
      "area": "API integration & microservices",
      "status": "Covered",
      "where": "Modules 2, 12, 16"
    },
    {
      "area": "Database design & deployment",
      "status": "Covered",
      "where": "PostgreSQL, Redis, pgvector in Module 14"
    },
    {
      "area": "Cloud architecture: AWS/Azure",
      "status": "Covered",
      "where": "Module 15 + Cloud FDE Track"
    },
    {
      "area": "Agentic orchestration: LangGraph, CrewAI",
      "status": "Covered",
      "where": "Modules 9-10"
    },
    {
      "area": "Agentic orchestration: Semantic Kernel",
      "status": "FDE roadmap",
      "where": "FDE Technical Stack Track"
    },
    {
      "area": "Advanced prompting: CoT/ReAct/DSPy",
      "status": "FDE roadmap",
      "where": "Prompting module + ReAct/DSPy videos"
    },
    {
      "area": "Vector search: Pinecone, Weaviate, Qdrant",
      "status": "Covered",
      "where": "Modules 4-5"
    },
    {
      "area": "LLMOps: LangSmith",
      "status": "Covered",
      "where": "Module 11"
    },
    {
      "area": "LLMOps: Helicone, Weights & Biases",
      "status": "FDE roadmap",
      "where": "FDE Technical Stack Track"
    },
    {
      "area": "Deployment: Docker/Kubernetes/cloud",
      "status": "Covered",
      "where": "Modules 13 and 15"
    },
    {
      "area": "Deployment: Modal, Replicate, Together AI, BentoML",
      "status": "FDE roadmap",
      "where": "FDE Technical Stack Track"
    },
    {
      "area": "Safety: NeMo Guardrails",
      "status": "Covered",
      "where": "Module 18"
    },
    {
      "area": "Safety: PII detection",
      "status": "FDE roadmap",
      "where": "FDE Technical Stack Track"
    },
    {
      "area": "Problem diagnosis frameworks",
      "status": "FDE roadmap",
      "where": "FDE Consulting And Delivery Track"
    },
    {
      "area": "Client discovery methodologies",
      "status": "FDE roadmap",
      "where": "FDE Consulting And Delivery Track"
    },
    {
      "area": "Stakeholder communication",
      "status": "FDE roadmap",
      "where": "FDE Consulting And Delivery Track"
    },
    {
      "area": "Solution scoping workshops",
      "status": "FDE roadmap",
      "where": "FDE Consulting And Delivery Track"
    },
    {
      "area": "Business case development",
      "status": "FDE roadmap",
      "where": "FDE Consulting And Delivery Track"
    },
    {
      "area": "Technical writing for non-tech clients",
      "status": "FDE roadmap",
      "where": "FDE Consulting And Delivery Track"
    },
    {
      "area": "Change management and AI adoption",
      "status": "FDE roadmap",
      "where": "FDE Consulting And Delivery Track"
    },
    {
      "area": "Success metrics and reporting",
      "status": "Partially covered",
      "where": "FDE page + evaluation/observability modules"
    },
    {
      "area": "Contract negotiation and scoping",
      "status": "Partial / role-dependent",
      "where": "Added as consulting readiness topic; not a deep legal course"
    },
    {
      "area": "LLM fine-tuning",
      "status": "Intentionally light",
      "where": "Original goal avoids model training; curriculum covers prompt/program optimization and deployment instead"
    }
  ],
  "fdeAcademyTechnicalTrack": {
    "title": "FDE Technical Stack Track",
    "subtitle": "Learn the FDE-adjacent technical stack: Semantic Kernel, ReAct/DSPy, Helicone, W&B, Modal, Replicate, Together AI, BentoML, and PII detection.",
    "coverage": [
      "Semantic Kernel",
      "ReAct",
      "DSPy",
      "Helicone",
      "Weights & Biases",
      "Modal",
      "Replicate",
      "Together AI",
      "BentoML",
      "PII detection"
    ],
    "videos": [
      {
        "title": "Building AI Agent Workflows with Semantic Kernel",
        "creator": "Microsoft Developer",
        "url": "https://www.youtube.com/watch?v=3JFKwerYj04",
        "duration": "19m 8s",
        "why": "Best official Microsoft video for adding Semantic Kernel to the orchestration landscape."
      },
      {
        "title": "ReAct Prompting",
        "creator": "Arize AI",
        "url": "https://www.youtube.com/watch?v=PB7hrp0mz54",
        "duration": "11m 21s",
        "why": "Compact practical explanation of ReAct, the prompt pattern behind many tool-using agents."
      },
      {
        "title": "DSPy: The End of Prompt Engineering",
        "creator": "AI Engineer",
        "url": "https://www.youtube.com/watch?v=-cKUW6n8hBU",
        "duration": "1h 13m",
        "why": "Best respected practitioner talk for DSPy and programmatic prompt optimization."
      },
      {
        "title": "Helicone AI - Open-source LLM Observability for Developers",
        "creator": "Helicone AI",
        "url": "https://www.youtube.com/watch?v=RNFa8bl3RdE",
        "duration": "5m 2s",
        "why": "Official Helicone overview for request logging, cost, latency, and analytics."
      },
      {
        "title": "LLMOps in action: Streamlining the path from prototype to production",
        "creator": "Weights & Biases",
        "url": "https://www.youtube.com/watch?v=E1DTsgbZPhw",
        "duration": "40m 49s",
        "why": "Official W&B LLMOps session for production experiment tracking, evals, and monitoring."
      },
      {
        "title": "Getting started with Modal",
        "creator": "Modal",
        "url": "https://www.youtube.com/watch?v=Y7n8sIZV1vQ",
        "duration": "11m 18s",
        "why": "Official Modal starter for serverless Python compute/GPU style deployment."
      },
      {
        "title": "Python for AI #5: AI APIs - ChatGPT, OpenAI, AssemblyAI, and Replicate",
        "creator": "AssemblyAI",
        "url": "https://www.youtube.com/watch?v=LGNvnu2imPo",
        "duration": "24m 22s",
        "why": "Practical Replicate API integration from a credible AI developer channel."
      },
      {
        "title": "How to run open source AI models on Together AI",
        "creator": "Together AI",
        "url": "https://www.youtube.com/watch?v=RiaundHy1Xc",
        "duration": "4m 12s",
        "why": "Official Together AI orientation for inference and open-source model serving."
      },
      {
        "title": "BentoML Tutorial: Build Production Grade AI Applications",
        "creator": "Krish Naik",
        "url": "https://www.youtube.com/watch?v=i_FtfdOKa2M",
        "duration": "29m 3s",
        "why": "Practical BentoML deployment path from a known AI engineering creator."
      },
      {
        "title": "WSO2 AI Guardrails: PII Masking, Prompt Injection & Safety",
        "creator": "WSO2",
        "url": "https://www.youtube.com/watch?v=ptRhf5pUEr0",
        "duration": "8m 50s",
        "why": "Direct PII masking and safety coverage missing from the original roadmap."
      }
    ]
  },
  "fdeAcademyConsultingTrack": {
    "title": "FDE Consulting And Delivery Track",
    "subtitle": "Learn the consulting and customer-delivery side of the FDE role: discovery, diagnosis, business value, stakeholder communication, change management, and trusted-advisor behavior.",
    "coverage": [
      "Problem diagnosis",
      "Client discovery",
      "Scoping workshops",
      "Business case",
      "Stakeholder communication",
      "Technical writing",
      "Change management",
      "AI adoption"
    ],
    "videos": [
      {
        "title": "Generative AI Product Discovery Sprint: Use Cases & Business Value Exploration",
        "creator": "AtabeyX Ventures",
        "url": "https://www.youtube.com/watch?v=BBEDnRAgSpY",
        "duration": "1h 52m",
        "why": "Long but useful workshop-style coverage for discovering high-value AI use cases."
      },
      {
        "title": "Integrating Generative AI Into Business Strategy",
        "creator": "MIT Corporate Relations",
        "url": "https://www.youtube.com/watch?v=9RvWcXVaAng",
        "duration": "50m 47s",
        "why": "Strong business-value framing for AI initiatives and executive conversations."
      },
      {
        "title": "How to Drive AI Adoption Internally",
        "creator": "Boston Consulting Group",
        "url": "https://www.youtube.com/watch?v=hd6L4DRWgvY",
        "duration": "4m 22s",
        "why": "Concise consulting-quality view of AI adoption and organizational rollout."
      },
      {
        "title": "Explaining Technical Information to Non-Technical People",
        "creator": "Engineer Man",
        "url": "https://www.youtube.com/watch?v=pGK2EuLXL7A",
        "duration": "6m 7s",
        "why": "Practical communication pattern for translating technical ideas to non-technical clients."
      },
      {
        "title": "How to Explain Technical Concepts to Non-Technical Stakeholders",
        "creator": "Clear English Communication",
        "url": "https://www.youtube.com/watch?v=6Fx2dDs4qdU",
        "duration": "5m 10s",
        "why": "Useful stakeholder communication technique for FDE demos and executive updates."
      },
      {
        "title": "AI & Change Management: What it means for Change Managers",
        "creator": "APMG International",
        "url": "https://www.youtube.com/watch?v=3biaqcutASs",
        "duration": "54m 16s",
        "why": "Deeper change-management context for AI adoption programs and long-term client engagement."
      }
    ]
  },
  "fdeBrochureSpecializationTrack": {
    "title": "FDE Product, Data And Multi-Tenant Delivery Track",
    "subtitle": "Brochure-aligned FDE additions: AI-first frontend demos, advanced data engineering for RAG, multi-tenant SaaS delivery, and human-in-the-loop customer workflows.",
    "coverage": [
      "AI-first frontends",
      "React/TypeScript AI apps",
      "Advanced data engineering",
      "RAG ingestion pipelines",
      "Multi-tenant SaaS",
      "Roles/permissions",
      "HITL",
      "Customer demo readiness"
    ],
    "videos": [
      {
        "title": "Full Stack AI Web App Tutorial (TypeScript/React/AI/LLMs)",
        "creator": "Tech With Tim",
        "url": "https://www.youtube.com/watch?v=kel893RIvHA",
        "duration": "1h 10m",
        "why": "Strong AI-first frontend project for building customer-facing demo/product surfaces."
      },
      {
        "title": "Build a RAG Chatbot from Scratch | React, Next.js, AI SDK, AI Elements, Neon, Drizzle, Clerk",
        "creator": "Codevolution",
        "url": "https://www.youtube.com/watch?v=3E5OxozYuA8",
        "duration": "1h 11m",
        "why": "Modern React/TypeScript RAG app with auth and product-style UI pieces."
      },
      {
        "title": "Build RAG Pipeline From Scratch - Data Ingestion to Vector DB Pipeline",
        "creator": "Krish Naik",
        "url": "https://www.youtube.com/watch?v=MykcjWPJ6T4",
        "duration": "59m",
        "why": "Brochure-aligned advanced data engineering: ingestion pipeline to vector database."
      },
      {
        "title": "How to Build a RAG Pipeline with Apache Airflow (Step-by-Step)",
        "creator": "The Data and AI Guy",
        "url": "https://www.youtube.com/watch?v=UBz5bxIDZdo",
        "duration": "13m",
        "why": "Adds scheduled/orchestrated data-pipeline thinking for enterprise RAG freshness."
      },
      {
        "title": "Multi-tenant Architecture for SaaS",
        "creator": "CodeOpinion",
        "url": "https://www.youtube.com/watch?v=e8k6TynqGFs",
        "duration": "11m",
        "why": "Clear architecture primer for multi-tenant isolation choices."
      },
      {
        "title": "Human in the loop (HITL) using LangGraph",
        "creator": "CampusX",
        "url": "https://www.youtube.com/watch?v=xxqZzVZ4gE0",
        "duration": "40m",
        "why": "Brochure-aligned HITL project for enterprise approval and escalation workflows."
      },
      {
        "title": "LangGraph Agents - Human-In-The-Loop - User Feedback",
        "creator": "LangChain",
        "url": "https://www.youtube.com/watch?v=YmAaKKlDy7k",
        "duration": "6m",
        "why": "Official LangChain HITL pattern for agent approval loops."
      }
    ]
  },
  "fdeReliabilityElectiveTrack": {
    "title": "FDE Reliability Elective: Multi-Region AWS, Storage And Resilience",
    "subtitle": "Brochure elective coverage for multi-region cloud, storage architecture, disaster recovery, sharding, CDN, high availability, and fault tolerance.",
    "coverage": [
      "AWS multi-region",
      "Disaster recovery",
      "Storage architecture",
      "Sharding",
      "CDN",
      "High availability",
      "Fault tolerance",
      "RTO/RPO"
    ],
    "videos": [
      {
        "title": "AWS re:Invent 2022 - Multi-Region design patterns and best practices",
        "creator": "AWS Events",
        "url": "https://www.youtube.com/watch?v=ilgpzlE7Hds",
        "duration": "58m",
        "why": "Best AWS-native deep dive for multi-region design patterns."
      },
      {
        "title": "AWS re:Invent 2025 - Multi-Region disaster recovery & resilience testing",
        "creator": "AWS Events",
        "url": "https://www.youtube.com/watch?v=jZIaliFsPIw",
        "duration": "51m",
        "why": "Current resilience and DR thinking for customer production environments."
      },
      {
        "title": "Back to Basics: How to Implement a Multi-Region Disaster Recovery Strategy Using AWS DRS",
        "creator": "Amazon Web Services",
        "url": "https://www.youtube.com/watch?v=OT1EJ_kyP_g",
        "duration": "4m",
        "why": "Official concise DR implementation orientation."
      },
      {
        "title": "The Ultimate Guide to Disaster Recovery: RTO, RPO, & Failover!",
        "creator": "ByteMonk",
        "url": "https://www.youtube.com/watch?v=OmASCUJEVy8",
        "duration": "11m",
        "why": "Clear operational concepts for incident response and recovery planning."
      },
      {
        "title": "The Basics of Database Sharding and Partitioning in System Design",
        "creator": "Exponent",
        "url": "https://www.youtube.com/watch?v=be6PLMKKSto",
        "duration": "6m",
        "why": "Focused sharding/partitioning refresher for storage-scale decisions."
      },
      {
        "title": "Design Patterns for High Availability: What gets you 99.999% uptime?",
        "creator": "Gaurav Sen",
        "url": "https://www.youtube.com/watch?v=LdvduBxZRLs",
        "duration": "13m",
        "why": "Strong reliability architecture primer for HA and fault tolerance."
      }
    ]
  },
  "fdeLabOperationsTrack": {
    "title": "FDE Lab Operations: Integration, Debugging, Drift, Security And Performance",
    "subtitle": "Image-aligned FDE lab coverage: integration harnesses, live debugging, CDC/reverse ETL, quality gates, drift monitoring, fine-tuning strategy, LLM routing, cross-account access, compliance, hardening, and load testing.",
    "coverage": [
      "Integration harnesses",
      "Contract testing",
      "Live debugging",
      "Logs/metrics/traces",
      "CDC",
      "Reverse ETL",
      "Quality gates",
      "Model drift",
      "Fine-tuning strategy",
      "LLM routing",
      "Cross-account access",
      "Compliance/privacy",
      "Load testing"
    ],
    "videos": [
      {
        "title": "Please Learn How To Write Tests in Python - Pytest Tutorial",
        "creator": "Tech With Tim",
        "url": "https://www.youtube.com/watch?v=EgpLj86ZHFQ",
        "duration": "33m",
        "why": "Base skill for building production-grade integration harnesses and regression checks."
      },
      {
        "title": "Contract testing and how Pact works",
        "creator": "PactFlow",
        "url": "https://www.youtube.com/watch?v=IetyhDr48RI",
        "duration": "11m",
        "why": "Strong fit for FDE integration harnesses across customer APIs and internal services."
      },
      {
        "title": "Exploring logs, metrics, and traces with Grafana",
        "creator": "Grafana",
        "url": "https://www.youtube.com/watch?v=1q3YzX2DDM4",
        "duration": "13m",
        "why": "Practical production debugging foundation using logs, metrics, and traces."
      },
      {
        "title": "Stream your PostgreSQL changes into Kafka with Debezium",
        "creator": "Code with Irtiza",
        "url": "https://www.youtube.com/watch?v=YZRHqRznO-o",
        "duration": "13m",
        "why": "Hands-on CDC pattern for advanced FDE data pipelines."
      },
      {
        "title": "What is Reverse ETL? Explained in 3 Minutes",
        "creator": "Hightouch",
        "url": "https://www.youtube.com/watch?v=BjlCxON_L5U",
        "duration": "4m",
        "why": "Clear explanation of reverse ETL, useful for CRM/ticketing/customer workflow integrations."
      },
      {
        "title": "Run data drift and model quality checks in an Airflow pipeline",
        "creator": "Evidently AI",
        "url": "https://www.youtube.com/watch?v=YHO7k3T_fZA",
        "duration": "29m",
        "why": "Direct match for data quality gates, drift checks, and pipeline monitoring."
      },
      {
        "title": "Evidently AI Tutorial - Open Source ML Models Monitoring and Observability",
        "creator": "Krish Naik",
        "url": "https://www.youtube.com/watch?v=cgc3dSEAel0",
        "duration": "30m",
        "why": "Practical model drift/model quality monitoring for MLOps."
      },
      {
        "title": "RAG vs. Fine Tuning",
        "creator": "IBM Technology",
        "url": "https://www.youtube.com/watch?v=00Q0G84kq3M",
        "duration": "9m",
        "why": "Good client-facing strategy video for when fine-tuning is appropriate versus RAG."
      },
      {
        "title": "Fine-Tune GPT-4o Model Step by Step",
        "creator": "Pradip Nichite",
        "url": "https://www.youtube.com/watch?v=jiYqbEDPw7A",
        "duration": "15m",
        "why": "Practical fine-tuning implementation reference without turning the curriculum into model training."
      },
      {
        "title": "How to Build Your Own Model Router",
        "creator": "AI Council",
        "url": "https://www.youtube.com/watch?v=ju7kKGVQRi0",
        "duration": "16m",
        "why": "Practical model routing pattern for cost, latency, and quality tradeoffs."
      },
      {
        "title": "AWS - Cross Account access using IAM role",
        "creator": "knowledgeindia - LearnCloud",
        "url": "https://www.youtube.com/watch?v=n1r9Fp7GKvk",
        "duration": "24m",
        "why": "Directly matches cross-account access and attack-surface concerns for customer cloud deployments."
      },
      {
        "title": "Security & AI Governance: Reducing Risks in AI Systems",
        "creator": "IBM Technology",
        "url": "https://www.youtube.com/watch?v=4QXtObc61Lw",
        "duration": "15m",
        "why": "Good compliance/governance framing for deployed AI systems."
      },
      {
        "title": "Promptfoo Red Teaming: Decoding LLM Security Architecture",
        "creator": "GenAI Learning",
        "url": "https://www.youtube.com/watch?v=Q3z4EEd2VcQ",
        "duration": "38m",
        "why": "Hands-on AI security audit/red-team style workflow."
      },
      {
        "title": "Load Testing FastAPI with Locust Python",
        "creator": "JCharisTech",
        "url": "https://www.youtube.com/watch?v=esIEW0aEKqk",
        "duration": "21m",
        "why": "Practical performance engineering for load testing AI backend APIs."
      }
    ]
  }
};
