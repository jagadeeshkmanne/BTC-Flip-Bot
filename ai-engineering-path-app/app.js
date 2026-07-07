(function () {
  const { useEffect, useMemo, useState } = React;
  const h = React.createElement;
  const data = window.CURRICULUM_DATA;
  const STORAGE_KEY = "production-ai-engineering-progress-v1";

  function getRoute() {
    const hash = window.location.hash.replace(/^#\/?/, "");
    if (!hash) return { page: "curriculum" };
    const parts = hash.split("/");
    if (parts[0] === "ai-engineer") return { page: "curriculum" };
    if (parts[0] === "curriculum") return { page: "curriculum" };
    if (parts[0] === "courses") return { page: "courses" };
    if (parts[0] === "module") return { page: "module", id: Number(parts[1] || 1) };
    if (parts[0] === "projects") return { page: "projects" };
    if (parts[0] === "fde") return { page: "fde" };
    return { page: "curriculum" };
  }

  function moduleTitle(module) {
    return module.desktop && module.desktop.title ? module.desktop.title : module.name;
  }

  function youtubeId(url) {
    const value = String(url || "");
    const match = value.match(/[?&]v=([^&]+)/) || value.match(/youtu\.be\/([^?&]+)/);
    return match ? match[1] : "";
  }

  function slug(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "");
  }

  function pct(done, total) {
    return total ? Math.round((done / total) * 100) : 0;
  }

  function readCompleted() {
    try {
      return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"));
    } catch (error) {
      return new Set();
    }
  }

  function itemKey(prefix, video) {
    return prefix + ":" + (video.id || youtubeId(video.url) || slug(video.title));
  }

  function moduleItems(module) {
    const items = [];
    (module.videos || []).forEach((video) => items.push(itemKey("module-" + module.num + "-lesson", video)));
    (module.projectVideos || []).forEach((video) => items.push(itemKey("module-" + module.num + "-project", video)));
    (module.supplementalTracks || []).forEach((track) => {
      (track.videos || []).forEach((video) => items.push(itemKey("module-" + module.num + "-track-" + track.id, video)));
    });
    if (module.num === 12 && data.claudeFdeTrack) {
      (data.claudeFdeTrack.videos || []).forEach((video) => items.push(itemKey("module-12-claude-tooling", video)));
    }
    return items;
  }

  function fdeItems() {
    const items = [];
    [
      data.fdeCloudTrack,
      awsOperationsTrack(),
      fdeEnterpriseDeliveryTrack(),
      enterpriseApmTrack(),
      data.fdeLabOperationsTrack,
      data.fdeAcademyTechnicalTrack,
      data.fdeAcademyConsultingTrack
    ].filter(Boolean).forEach((track) => {
      (track.videos || []).forEach((video) => items.push(itemKey("fde-" + slug(track.title), video)));
    });
    return items;
  }

  function allItems() {
    return data.modules.flatMap(moduleItems).concat(fdeItems());
  }

  function progressFor(keys, completed) {
    const done = keys.filter((key) => completed.has(key)).length;
    return { done, total: keys.length, percent: pct(done, keys.length) };
  }

  const roadmapPhases = [
    {
      label: "Course 1",
      title: "Foundations And LLM API Contracts",
      modules: [1, 2, 3],
      focus: ["Python", "LLM APIs", "Prompting", "Structured outputs", "Tool calling"],
      outcome: "Start here. Learn the runtime, provider API shape, prompts, typed output contracts, and safe tool calls before building larger systems."
    },
    {
      label: "Course 2",
      title: "Product Backend And Enterprise Data",
      modules: [4, 5, 6],
      focus: ["FastAPI", "Auth", "Streaming", "PostgreSQL", "Redis"],
      outcome: "Then wrap the model in a service: API endpoints, auth, streaming, relational persistence, caching, queues, and tenant-aware data foundations."
    },
    {
      label: "Course 3",
      title: "Retrieval And Knowledge Systems",
      modules: [7, 8, 9, 10],
      focus: ["Embeddings", "Vector databases", "RAG", "Hybrid search", "Reranking"],
      outcome: "Now build the enterprise knowledge layer: embeddings, vector databases, document ingestion, RAG, hybrid search, reranking, and retrieval quality."
    },
    {
      label: "Course 4",
      title: "Orchestration, Agents And Workflows",
      modules: [11, 12, 13],
      focus: ["LangChain", "LlamaIndex", "LangGraph", "MCP", "CrewAI", "AG2/AutoGen", "n8n"],
      outcome: "After retrieval works, add orchestration: framework boundaries, state machines, MCP, agents, multi-agent systems, CrewAI, AG2/AutoGen, and n8n workflows."
    },
    {
      label: "Course 5",
      title: "Quality, Observability And Safety",
      modules: [14, 15, 16],
      focus: ["Workers", "Evaluation", "LangSmith", "Langfuse", "OpenTelemetry", "Guardrails"],
      outcome: "Before production, add the controls: async jobs, evals, tracing, cost/debug telemetry, guardrails, safety checks, and operational visibility."
    },
    {
      label: "Course 6",
      title: "Deployment, Cloud And Scale",
      modules: [17, 18, 19],
      focus: ["Docker", "Kubernetes", "AWS Bedrock", "Azure AI", "Vertex AI", "Scaling", "Cost", "Security"],
      outcome: "Finally deploy and operate at enterprise scale: containers, Kubernetes, cloud model platforms, IAM, cost optimization, security, and production architecture."
    },
    {
      label: "Course 7",
      title: "Enterprise Capstone Portfolio",
      modules: [20],
      focus: ["End-to-end build", "Portfolio", "Architecture writeup", "Demo readiness"],
      outcome: "Finish with a portfolio-grade enterprise system that proves you can design, build, operate, and explain production AI applications."
    },
    {
      label: "Specialization",
      title: "Forward Deployed Engineer Path",
      modules: [],
      focus: ["Customer discovery", "Cloud deployment", "SRE ownership", "Adoption"],
      outcome: "After the core course, add AWS-first cloud deployment, customer discovery, governance, stakeholder communication, and post-launch ownership."
    }
  ];

  function moduleByNum(num) {
    return data.modules.find((item) => item.num === num);
  }

  function phaseModuleLabel(phase) {
    if (!phase.modules.length) return "FDE specialization";
    if (phase.modules.length === 1) return "Module " + phase.modules[0];
    return "Modules " + phase.modules[0] + "-" + phase.modules[phase.modules.length - 1];
  }

  function phaseId(phase) {
    return "course-" + slug(phase.label || phase.title);
  }

  function phaseItems(phase) {
    if (phase.modules.length) return phase.modules.flatMap((num) => moduleItems(moduleByNum(num)));
    return fdeItems();
  }

  function pill(text) {
    return h("span", { className: "pill", key: text }, text);
  }

  function App() {
    const [route, setRoute] = useState(getRoute());
    const [completed, setCompleted] = useState(readCompleted);
    const allProgressItems = useMemo(() => allItems(), []);
    const courseProgress = progressFor(allProgressItems, completed);

    useEffect(() => {
      const onHashChange = () => setRoute(getRoute());
      window.addEventListener("hashchange", onHashChange);
      return () => window.removeEventListener("hashchange", onHashChange);
    }, []);

    function toggleCompleted(key) {
      setCompleted((current) => {
        const next = new Set(current);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
        return next;
      });
    }

    return h(React.Fragment, null,
      h(Header, { route, courseProgress }),
      route.page === "curriculum" && h(CurriculumPage, { completed, toggleCompleted }),
      route.page === "courses" && h(CoursesPage, { completed }),
      route.page === "module" && h(ModulePage, { id: route.id, completed, toggleCompleted }),
      route.page === "projects" && h(ProjectsPage),
      route.page === "fde" && h(FdePage, { completed, toggleCompleted }),
      h("footer", { className: "footer-note" },
        "Static React app for GitHub Pages. Video-first curriculum using curated YouTube lessons, project walkthroughs, and optional reference material."
      )
    );
  }

  function Header({ route, courseProgress }) {
    const items = [
      ["#/", "Learning Path", "curriculum"]
    ];
    const activePage = route.page === "module" ? "curriculum" : route.page;
    return h("header", { className: "site-header" },
      h("div", { className: "header-inner" },
        h("a", { className: "brand", href: "#/" },
          h("div", { className: "brand-mark" }, "AI"),
          h("span", null, "Production AI Engineering")
        ),
        h("button", {
          className: "mobile-menu-button",
          onClick: () => window.dispatchEvent(new CustomEvent("toggle-roadmap-menu")),
          "aria-label": "Open roadmap menu"
        }, "Menu"),
        h("nav", { className: "top-nav", "aria-label": "Course pages" },
          items.map(([href, label, page]) => h("a", {
            key: href,
            href,
            className: activePage === page ? "active" : ""
          }, label))
        ),
        h("div", { className: "header-progress", "aria-label": "Course progress" },
          h("span", null, courseProgress.percent + "% complete"),
          h("div", { className: "mini-progress" },
            h("div", { style: { width: courseProgress.percent + "%" } })
          )
        )
      )
    );
  }

  function RoadmapOverview({ completed }) {
    return h("section", { className: "section" },
      h("h2", { className: "section-title" }, "Roadmap Order"),
      h("p", { className: "section-lead" },
        "Follow the phases in order. Each phase depends on the previous one, so do not jump to agents, cloud, or FDE before the retrieval and backend foundations are comfortable."
      ),
      h("div", { className: "phase-stack compact-phase-stack" },
        roadmapPhases.map((phase, index) => {
          const progress = progressFor(phaseItems(phase), completed);
          return h("div", { className: "phase-card", key: phase.title },
            h("div", { className: "phase-kicker" }, "Step " + (index + 1)),
            h("h3", null, phase.title),
            h("p", null, phase.outcome),
            h("div", { className: "phase-modules" },
              phase.modules.length
                ? phase.modules.map((num) => h("a", { href: "#/module/" + num, key: num }, "M" + num))
                : h("a", { href: "#/fde" }, "FDE")
            ),
            completed.size > 0 && h("div", { className: "module-progress" },
              h("div", { className: "progress-row" },
                h("span", null, progress.done + "/" + progress.total),
                h("strong", null, progress.percent + "%")
              ),
              h("div", { className: "progress-bar" },
                h("div", { style: { width: progress.percent + "%" } })
              )
            )
          );
        })
      )
    );
  }

  function HowToUsePath({ mode }) {
    const items = mode === "fde"
      ? [
        ["Step 1", "Finish AI Engineer Roadmap", "FDE assumes you can already build production AI apps."],
        ["Step 2", "Follow FDE Specialization", "Work through discovery, cloud deployment, LLMOps, security, scaling, and adoption."],
        ["Step 3", "Use Paid Links Only As Optional Checks", "Udemy references are supplemental; the roadmap and projects remain the source of truth."]
      ]
      : [
        ["Step 1", "Follow The Roadmap In Order", "Each section depends on the previous one."],
        ["Step 2", "Watch Lessons Then Projects", "Use project walkthroughs only after the core lessons in that topic."],
        ["Step 3", "Finish A Capstone", "Use the final build as portfolio proof before applying."]
      ];
    return h("section", { className: "section compact-section" },
      h("div", { className: "path-explainer" },
        items.map((item) => h("div", { className: "path-explainer-item", key: item[0] },
          h("strong", null, item[0]),
          h("h3", null, item[1]),
          h("p", null, item[2])
        ))
      )
    );
  }

  function learningSections() {
    const section = (label, title, subtitle, focus, moduleNums, tracks) => ({
      id: "learn-" + slug(title),
      label,
      title,
      subtitle,
      focus,
      modules: moduleNums.map(moduleByNum).filter(Boolean),
      tracks: (tracks || []).filter(Boolean)
    });
    return [
      section(
        "Step 1",
        "Foundations And LLM API Contracts",
        "Start with the same foundation every company needs: Python runtime, provider APIs, prompt design, structured output contracts, and tool calling.",
        ["Python", "LLM APIs", "Prompting", "Structured outputs", "Tool calling"],
        [1, 2, 3],
        []
      ),
      section(
        "Step 2",
        "Product Backend And Enterprise Data",
        "Next create the application shell: FastAPI endpoints, auth, streaming, PostgreSQL, Redis, queues, and optional enterprise data platforms.",
        ["FastAPI", "Auth", "Streaming", "PostgreSQL", "Redis", "Databricks/Kafka"],
        [4, 5, 6],
        []
      ),
      section(
        "Step 3",
        "Retrieval And Knowledge Systems",
        "Once backend and data exist, build the knowledge layer: embeddings, vector stores, RAG, hybrid search, reranking, document ingestion, and retrieval quality.",
        ["Embeddings", "Vector DBs", "RAG", "Hybrid search", "Reranking", "Document intelligence"],
        [7, 8, 9, 10],
        []
      ),
      section(
        "Step 4",
        "Orchestration, Agents And Workflows",
        "After retrieval is reliable, add orchestration: LangChain, LlamaIndex, LangGraph, MCP, CrewAI, AG2/AutoGen, n8n, multi-agent patterns, and optional Claude tooling.",
        ["LangChain", "LlamaIndex", "LangGraph", "MCP", "CrewAI", "AG2/AutoGen", "n8n"],
        [11, 12, 13],
        []
      ),
      section(
        "Step 5",
        "Quality, Observability And Safety",
        "Before cloud rollout, make the system measurable and safer: background workers, evals, LangSmith, Langfuse, OpenTelemetry, Grafana, Splunk, Dynatrace, and guardrails.",
        ["Workers", "Evals", "LangSmith/Langfuse", "OpenTelemetry", "Grafana/Splunk/Dynatrace", "Guardrails"],
        [14, 15, 16],
        [enterpriseApmTrack()]
      ),
      section(
        "Step 6",
        "Deployment, Cloud And Scale",
        "Then deploy into enterprise environments: Docker, Kubernetes, CI/CD, Terraform/IaC, AWS Lambda/App Runner, Cloud Run, Azure Container Apps, Bedrock, Vertex, cost controls, and security.",
        ["Docker", "Kubernetes", "Terraform", "Serverless", "AWS Bedrock", "Azure/GCP", "Cost", "Security"],
        [17, 18, 19],
        [cloudDeploymentPatternsTrack(), awsOperationsTrack(), data.fdeCloudTrack]
      ),
      section(
        "Step 7",
        "FDE Customer Delivery And Enterprise Product",
        "Finally add the forward-deployed layer: discovery, scoping, enterprise workflow delivery, auditability, HITL, stakeholder communication, rollout, adoption, and portfolio proof.",
        ["Discovery", "Scoping", "Auditability", "HITL", "Stakeholders", "Launch readiness", "Capstone"],
        [20],
        [data.fdeAcademyConsultingTrack, fdeEnterpriseDeliveryTrack(), fdeOperatingModelTrack()]
      )
    ];
  }

  function sectionVideoKeys(section) {
    const moduleKeys = (section.modules || []).flatMap(moduleItems);
    const trackKeys = (section.tracks || []).flatMap((track) =>
      (track.videos || []).map((video) => itemKey(track.keyPrefix || "fde-" + slug(track.title), video))
    );
    return moduleKeys.concat(trackKeys);
  }

  function cloudDeploymentPatternsTrack() {
    return {
      title: "Optional Cloud Deployment Patterns: IaC, Serverless And Multi-Cloud",
      subtitle: "Added from the production Udemy outline where it genuinely helps: Terraform, Lambda/API Gateway, App Runner, Cloud Run, Azure Container Apps, EventBridge, Aurora Serverless, and AWS cost monitoring.",
      coverage: ["Terraform", "AWS Lambda", "API Gateway", "App Runner", "Cloud Run", "Azure Container Apps", "EventBridge", "Aurora Serverless", "AWS budgets"],
      videos: [
        {
          title: "Terraform explained in 15 mins | Terraform Tutorial for Beginners",
          creator: "TechWorld with Nana",
          url: "https://www.youtube.com/watch?v=l5k1ai_GBDE",
          duration: "18m 15s",
          why: "Best concise IaC foundation before deploying AI services repeatedly across dev/test/prod."
        },
        {
          title: "Deploy FastAPI on AWS Lambda | In 9 MINUTES",
          creator: "Eric Roby",
          url: "https://www.youtube.com/watch?v=7-CvGFJNE_o",
          duration: "9m 2s",
          why: "Covers the AWS Lambda deployment pattern used heavily in the Udemy production outline."
        },
        {
          title: "Deploy Fine-tuned Transformers Model with FastAPI on AWS App Runner",
          creator: "Pradip Nichite",
          url: "https://www.youtube.com/watch?v=ACQtRi-bAqg",
          duration: "32m 3s",
          why: "Practical App Runner container deployment path for Python AI APIs without managing Kubernetes."
        },
        {
          title: "Deploy a FastAPI App to Google Cloud Run with uv and Docker",
          creator: "Mazlum | GCP, Software & Data",
          url: "https://www.youtube.com/watch?v=mcaYN2tb7SQ",
          duration: "25m 44s",
          why: "Adds the GCP Cloud Run deployment pattern from the Udemy production outline."
        },
        {
          title: "Deploy a Python App with Docker on Azure Cloud - Container Apps",
          creator: "Tomek in Tech",
          url: "https://www.youtube.com/watch?v=2q_EA98kDGg",
          duration: "12m 34s",
          why: "Adds the Azure Container Apps deployment pattern without turning the course into an Azure certification track."
        },
        {
          title: "How to Schedule Lambda Function using Amazon EventBridge",
          creator: "AWS Made Easy",
          url: "https://www.youtube.com/watch?v=bDnnNfIUdxk",
          duration: "4m 16s",
          why: "Useful for scheduled ingestion, recurring agents, cleanup jobs, and operational automations."
        },
        {
          title: "AWS Aurora Serverless Tutorial | Step By Step",
          creator: "Be A Better Dev",
          url: "https://www.youtube.com/watch?v=ciRbXZqBl7M",
          duration: "16m 25s",
          why: "Covers the serverless relational database pattern mentioned in the production AI course outline."
        },
        {
          title: "Cost Management Using AWS Budget - AWS CCP The Easy Way",
          creator: "MAKERDEMY",
          url: "https://www.youtube.com/watch?v=vjLNEKITrvE",
          duration: "6m 13s",
          why: "A short practical cost guardrail for cloud experiments and customer deployments."
        }
      ]
    };
  }

  function awsOperationsTrack() {
    return {
      title: "AWS Operations For Enterprise AI Apps",
      subtitle: "AWS operating skills that matter for FDE/customer deployments: IAM boundaries, private access, secrets, CloudWatch, CI/CD, and runtime configuration.",
      coverage: ["CloudWatch", "IAM", "VPC/private access", "Secrets", "GitHub Actions", "Kubernetes config"],
      videos: [

        {
          title: "AWS - Cross Account access using IAM role",
          creator: "AWS Made Easy",
          url: "https://www.youtube.com/watch?v=Qrm84k9vRXg",
          duration: "8m 52s",
          why: "Cross-account access is common in customer enterprise environments."
        },
        {
          title: "GitHub Actions Tutorial - Basic Concepts and CI/CD Pipeline with Docker",
          creator: "TechWorld with Nana",
          url: "https://www.youtube.com/watch?v=R8_veQiYBjI",
          duration: "32m 31s",
          why: "CI/CD bridge from code to deployed AI services."
        }
      ]
    };
  }

  function enterpriseDataPlatformTrack() {
    return {
      title: "Optional Enterprise Data Platforms: Databricks And Kafka",
      subtitle: "Optional depth for companies that expect lakehouse, streaming, CDC, or event-driven ingestion experience.",
      coverage: ["Databricks", "Kafka", "CDC", "Streaming", "Reverse ETL"],
      videos: [


      ]
    };
  }

  function enterpriseApmTrack() {
    return {
      title: "Enterprise APM: Grafana, Splunk And Dynatrace",
      subtitle: "Classic enterprise observability tools you may meet in customer environments, layered under LLM traces and evals.",
      coverage: ["Grafana", "Splunk", "Dynatrace", "OpenTelemetry", "Logs", "Metrics", "Traces"],
      videos: [


      ]
    };
  }

  function fdeEnterpriseDeliveryTrack() {
    const source = data.fdeBrochureSpecializationTrack;
    if (!source) return null;
    return {
      ...source,
      title: "FDE Enterprise Data, Multi-Tenant And HITL Delivery Track",
      subtitle: "Focus on enterprise RAG ingestion, permissions, integrations, and human-in-the-loop workflows. Frontend/full-stack refreshers remain out of the required path.",
      coverage: ["Data pipelines", "Permissions", "Integrations", "HITL", "Customer workflow readiness"],
      videos: filterKnownSkillsVideos(source.videos || [])
    };
  }

  function fdeOperatingModelTrack() {
    return {
      title: "FDE Operating Model: Metrics, Launch, Reporting And Incidents",
      subtitle: "Added from the FDE mastery outline: success metrics, requirements, launch readiness, demos, stakeholder reporting, incident response, rollback, and postmortems.",
      coverage: ["Success metrics", "Requirements", "Demos", "Status reporting", "Incident response", "Postmortems", "Launch readiness"],
      videos: [

        {
          title: "What Is A Design Doc In Software Engineering? (full example)",
          creator: "Clément Mihailescu",
          url: "https://www.youtube.com/watch?v=bgHL41e7vgI",
          duration: "16m 5s",
          why: "Design docs are how you turn discovery and architecture into customer-visible execution."
        },
        {
          title: "SRE Incident Management: Google's Reliability Approach",
          creator: "CodeLucky",
          url: "https://www.youtube.com/watch?v=vNm4XnfVu0Y",
          duration: "6m 8s",
          why: "Matches production debugging, incident response, escalation, rollback, and postmortem duties."
        }
      ]
    };
  }

  function filterKnownSkillsVideos(videos) {
    const skipped = [
      "Full Stack AI Web App Tutorial",
      "Build a RAG Chatbot from Scratch",
      "FastAPI + React B2B SaaS Full Project Build",
      "Build a Full-Stack GenAI Project",
      "Build Your AI Coding Assistant",
      "Multi-tenant Architecture for SaaS",
      "Database Sharding",
      "Sharding and Partitioning",
      "High Availability",
      "Storage architecture"
    ];
    return videos.filter((video) => !skipped.some((title) => (video.title || "").includes(title)));
  }

  function fdeScalingTopics() {
    return data.scalingTopics || [];
  }

  function CurriculumPage({ completed, toggleCompleted }) {
    const sections = learningSections();
    return h("main", { className: "page" },
      h("section", { className: "section page-hero" },
        h("div", { className: "eyebrow" }, "Single Page Playlist"),
        h("h1", null, "FDE Job Learning Path"),
        h("p", { className: "section-lead" },
          "Follow this exact order like a real enterprise AI build: language and LLM contracts first, then backend and data, then retrieval, agents, quality controls, deployment/cloud/scale, and finally customer delivery. Frontend/full-stack and generic system-design refreshers are intentionally removed from the main path."
        )
      ),
      h("div", { className: "learning-shell" },
        h(QuickJumpMenu, { sections, completed }),
        h("div", { className: "learning-content" },
          sections.map((section) => h(LearningVideoSection, {
            section,
            completed,
            toggleCompleted,
            key: section.id
          }))
        )
      )
    );
  }

  function QuickJumpMenu({ sections, completed }) {
    const [open, setOpen] = useState(false);
    useEffect(() => {
      const openMenu = () => setOpen(true);
      window.addEventListener("toggle-roadmap-menu", openMenu);
      return () => window.removeEventListener("toggle-roadmap-menu", openMenu);
    }, []);
    function jumpTo(sectionId) {
      setOpen(false);
      document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    return h("aside", { className: "quick-menu-wrap", "aria-label": "Learning path navigation" },
      open && h("button", {
        className: "quick-menu-backdrop",
        onClick: () => setOpen(false),
        "aria-label": "Close roadmap menu"
      }),
      h("div", { className: "quick-menu-panel" + (open ? " is-open" : "") },
        h("div", { className: "quick-menu-title" },
          h("span", null, "Roadmap"),
          h("strong", null, "Learn In Order"),
          h("button", {
            className: "quick-menu-close",
            onClick: () => setOpen(false),
            "aria-label": "Close roadmap menu"
          }, "Close")
        ),
        h("div", { className: "quick-menu" },
          sections.map((section) => {
            const progress = progressFor(sectionVideoKeys(section), completed);
            return h("button", {
              key: section.id,
              className: "quick-menu-item",
              onClick: () => jumpTo(section.id)
            },
              h("span", null, section.label),
              h("strong", null, section.title),
              h("em", null, progress.done + "/" + progress.total)
            );
          })
        )
      )
    );
  }

  function LearningVideoSection({ section, completed, toggleCompleted }) {
    const [playing, setPlaying] = useState(null);
    const progress = progressFor(sectionVideoKeys(section), completed);
    return h("section", { className: "section learning-section", id: section.id },
      h("div", { className: "learning-section-head" },
        h("div", null,
          h("div", { className: "phase-kicker" }, section.label),
          h("h2", { className: "section-title" }, section.title),
          h("p", { className: "section-lead" }, section.subtitle),
          h("div", { className: "topic-row" }, (section.focus || []).map(pill))
        ),
        h("div", { className: "phase-progress" },
          h("strong", null, progress.percent + "%"),
          h("span", null, progress.done + " of " + progress.total + " videos"),
          h("div", { className: "progress-bar" }, h("div", { style: { width: progress.percent + "%" } }))
        )
      ),
      (section.modules || []).map((module) => h(ModuleVideoBlock, {
        module,
        completed,
        toggleCompleted,
        playing,
        setPlaying,
        key: module.num
      })),
      (section.tracks || []).map((track) => h(TrackVideoBlock, {
        track,
        completed,
        toggleCompleted,
        playing,
        setPlaying,
        key: track.title
      })),
      section.title === "Deployment, Cloud And Scale" && h(PaidCourseReference),
      section.title === "FDE Customer Delivery And Enterprise Product" && h(FdePaidReferences),
      section.title === "FDE Customer Delivery And Enterprise Product" && h(InterviewPrepPdf)
    );
  }

  function PaidCourseReference() {
    const course = {
      badge: "Optional paid",
      label: "CLOUD",
      title: "Generative And Agentic AI In Production",
      meta: "Udemy | Paid | Cloud deployment companion",
      why: "Use after Modules 17-19 if you want structured reps for AWS/GCP/Azure deployment, Terraform/IaC, serverless deployment, monitoring, guardrails, and production cost controls.",
      skip: "Skip or skim Vercel, Next.js, frontend UI, subscription billing, generic full-stack setup, and beginner deployment explanations. Focus on AWS IAM, cost monitoring, Docker, App Runner, Lambda, API Gateway, S3, Bedrock, Terraform, CI/CD, Cloud Run, Azure Container Apps, CloudWatch, Langfuse, LLM-as-a-judge, prompt injection, AgentCore, and production tradeoffs.",
      url: "https://www.udemy.com/course/generative-and-agentic-ai-in-production/",
      action: "Open Udemy"
    };
    return h("article", { className: "video-topic-block track-topic-block" },
      h("div", { className: "video-topic-head" },
        h("div", null,
          h("div", { className: "module-number" }, "Optional Paid Course"),
          h("h3", null, "AI Engineer Production Track: Deploy LLMs & Agents at Scale"),
          h("p", null,
            "Use this Udemy course only as a structured deployment companion after Modules 17-19. It is useful for AWS/GCP/Azure deployment reps, Terraform/IaC, Lambda/App Runner/Cloud Run/Azure Container Apps, Bedrock, monitoring, guardrails, and production cost controls."
          )
        ),
        h("div", { className: "topic-row" },
          ["AWS", "GCP", "Azure", "Terraform", "CI/CD", "Bedrock", "Monitoring", "Guardrails"].map(pill)
        )
      ),
      h("div", { className: "lesson-list compact-lessons" },
        h("div", { className: "project-video-card paid-course-card" },
          h("div", { className: "course-thumb cloud-course-thumb", "aria-hidden": "true" },
            h("span", null, course.badge),
            h("strong", null, course.label)
          ),
          h("div", null,
            h("h3", null, course.title),
            h("div", { className: "lesson-meta" }, course.meta),
            h("p", { className: "lesson-copy" }, course.why),
            h("div", { className: "skip" }, "Skip: " + course.skip)
          ),
          h("div", { className: "lesson-actions" },
            h("a", {
              className: "button primary",
              href: course.url,
              target: "_blank",
              rel: "noreferrer"
            }, course.action)
          )
        )
      )
    );
  }

  function FdePaidReferences() {
    const courses = [
      {
        badge: "Optional paid",
        label: "FDE",
        title: "Forward Deployed Engineer Mastery",
        meta: "Udemy | Paid | Role and delivery overview",
        why: "Use after the full roadmap to reinforce discovery, scoping, stakeholder communication, delivery, launch readiness, and customer ownership.",
        skip: "Do not treat this as the main curriculum. It is too short to replace the AI, cloud, SRE, and FDE roadmap.",
        url: "https://www.udemy.com/course/forward-deployed-engineer-mastery/",
        action: "Open Udemy"
      },
      {
        badge: "Final check",
        label: "TEST",
        title: "Forward Deployed Engineering Certification Practice Test",
        meta: "Udemy | Paid | Final self-assessment",
        why: "Use only at the very end as a confidence check. Practice tests do not teach the job; they reveal weak spots after you have studied and built.",
        skip: "Skip until you complete the main path, cloud/deployment section, FDE delivery section, and at least one capstone.",
        url: "https://www.udemy.com/course/forward-deployed-engineering-certification-practice-test/",
        action: "Open Practice Test"
      }
    ];
    return h("article", { className: "video-topic-block track-topic-block" },
      h("div", { className: "video-topic-head" },
        h("div", null,
          h("div", { className: "module-number" }, "Optional Paid References"),
          h("h3", null, "FDE Role And Certification References"),
          h("p", null,
            "These are optional paid references placed at the end of the main path. Use them only after the YouTube-first curriculum if you want a role overview or a final self-check."
          )
        ),
        h("div", { className: "topic-row" }, ["FDE", "Discovery", "Delivery", "Assessment"].map(pill))
      ),
      h("div", { className: "lesson-list compact-lessons" },
        courses.map((course) => h("div", { className: "project-video-card paid-course-card", key: course.title },
          h("div", { className: "course-thumb", "aria-hidden": "true" },
            h("span", null, course.badge),
            h("strong", null, course.label)
          ),
          h("div", null,
            h("h3", null, course.title),
            h("div", { className: "lesson-meta" }, course.meta),
            h("p", { className: "lesson-copy" }, course.why),
            h("div", { className: "skip" }, "Skip: " + course.skip)
          ),
          h("div", { className: "lesson-actions" },
            h("a", {
              className: "button primary",
              href: course.url,
              target: "_blank",
              rel: "noreferrer"
            }, course.action)
          )
        ))
      )
    );
  }

  function InterviewPrepPdf() {
    return h("article", { className: "video-topic-block track-topic-block" },
      h("div", { className: "video-topic-head" },
        h("div", null,
          h("div", { className: "module-number" }, "Final Interview Prep"),
          h("h3", null, "AI Engineer And FDE Interview Questions PDF"),
          h("p", null,
            "Use this after finishing the roadmap and at least one capstone. Treat it as a final interview readiness checklist, not as a replacement for building projects."
          )
        ),
        h("div", { className: "topic-row" }, ["Interview prep", "FDE", "AI Engineer", "Final review"].map(pill))
      ),
      h("div", { className: "lesson-list compact-lessons" },
        h("div", { className: "project-video-card paid-course-card" },
          h("div", { className: "course-thumb pdf-course-thumb", "aria-hidden": "true" },
            h("span", null, "PDF"),
            h("strong", null, "Q&A")
          ),
          h("div", null,
            h("h3", null, "InterviewQuestions.pdf"),
            h("div", { className: "lesson-meta" }, "Local PDF | Final preparation | Open in browser"),
            h("p", { className: "lesson-copy" },
              "Review this at the end of the course to practice explaining RAG, agents, cloud deployment, LLMOps, security, scaling, and FDE customer delivery."
            ),
            h("div", { className: "skip" }, "Use after: Complete all roadmap sections, the cloud/deployment content, and at least one production capstone.")
          ),
          h("div", { className: "lesson-actions" },
            h("a", {
              className: "button primary",
              href: "./InterviewQuestions.pdf",
              target: "_blank",
              rel: "noreferrer"
            }, "Open PDF")
          )
        )
      )
    );
  }

  function ModuleVideoBlock({ module, completed, toggleCompleted, playing, setPlaying }) {
    const desktop = module.desktop || {};
    return h("article", { className: "video-topic-block" },
      h("div", { className: "video-topic-head" },
        h("div", null,
          h("div", { className: "module-number" }, "Topic " + module.num),
          h("h3", null, moduleTitle(module)),
          h("p", null, desktop.goal || "")
        ),
        h("div", { className: "topic-row" }, ((desktop.techs || []).slice(0, 6)).map(pill))
      ),
      h("h4", null, "Lessons"),
      h("div", { className: "lesson-list compact-lessons" },
        (module.videos || []).map((video) => h(Lesson, {
          key: video.id || video.url,
          video,
          itemKey: itemKey("module-" + module.num + "-lesson", video),
          completed,
          toggleCompleted,
          isPlaying: playing === (video.id || youtubeId(video.url)),
          setPlaying
        }))
      ),
      h("h4", null, "Project Builds"),
      h("div", { className: "lesson-list compact-lessons" },
        (module.projectVideos || []).map((video) => h(ProjectVideo, {
          key: video.url,
          video,
          itemKey: itemKey("module-" + module.num + "-project", video),
          completed,
          toggleCompleted,
          isPlaying: playing === youtubeId(video.url),
          setPlaying
        }))
      ),
      (module.supplementalTracks || []).map((track) => h(TrackVideoBlock, {
        key: track.id,
        track: {
          title: "Additional: " + track.title,
          subtitle: "Optional extra videos for this topic.",
          coverage: track.techs || [],
          videos: (track.videos || []).map((video) => ({
            title: video.step + ". " + video.title,
            creator: video.creator,
            url: video.url,
            duration: video.duration,
            why: video.whyBest,
            cost: video.difficulty
          })),
          keyPrefix: "module-" + module.num + "-track-" + track.id
        },
        completed,
        toggleCompleted,
        playing,
        setPlaying
      })),
      module.num === 12 && data.claudeFdeTrack && h(TrackVideoBlock, {
        track: Object.assign({ keyPrefix: "module-12-claude-tooling" }, data.claudeFdeTrack),
        completed,
        toggleCompleted,
        playing,
        setPlaying
      })
    );
  }

  function TrackVideoBlock({ track, completed, toggleCompleted, playing, setPlaying }) {
    const prefix = track.keyPrefix || (track.roleContext ? "fde-role" : "fde-" + slug(track.title));
    return h("article", { className: "video-topic-block track-topic-block" },
      h("div", { className: "video-topic-head" },
        h("div", null,
          h("div", { className: "module-number" }, "Track"),
          h("h3", null, track.title),
          h("p", null, track.subtitle || "")
        ),
        h("div", { className: "topic-row" }, (track.coverage || []).slice(0, 10).map(pill))
      ),
      h("div", { className: "lesson-list compact-lessons" },
        (track.videos || []).map((video) => h(ProjectVideo, {
          key: video.url,
          video: Object.assign({ cost: "Free YouTube" }, video),
          itemKey: itemKey(prefix, video),
          completed,
          toggleCompleted,
          isPlaying: playing === youtubeId(video.url),
          setPlaying
        }))
      )
    );
  }

  function PhaseSection({ phase, index, completed }) {
    const progress = progressFor(phaseItems(phase), completed);
    return h("section", { className: "roadmap-phase", id: phaseId(phase) },
      h("div", { className: "phase-head" },
        h("div", null,
          h("div", { className: "phase-kicker" }, phase.label || "Step " + (index + 1)),
          h("h2", null, phase.title),
          h("p", null, phase.outcome),
          h("div", { className: "phase-module-range" }, phaseModuleLabel(phase)),
          h("div", { className: "topic-row phase-focus" }, (phase.focus || []).map(pill))
        ),
        h("div", { className: "phase-progress" },
          h("strong", null, progress.percent + "%"),
          h("span", null, progress.done + " of " + progress.total + " videos"),
          h("div", { className: "progress-bar" },
            h("div", { style: { width: progress.percent + "%" } })
          )
        )
      ),
      phase.modules.length
        ? h("div", { className: "module-grid" },
          phase.modules.map((num) => h(ModuleCatalogCard, { module: moduleByNum(num), completed, key: num }))
        )
        : h("div", { className: "fde-roadmap-card" },
          h("h3", null, "After Module 20: FDE Readiness"),
          h("p", null, "Then learn AWS-first cloud deployment, customer discovery, stakeholder communication, governance, and post-launch ownership."),
          h("a", { className: "button primary", href: "#/fde" }, "Open FDE Path")
        )
    );
  }

  function ModuleCatalogCard({ module, completed }) {
    const desktop = module.desktop || {};
    const progress = progressFor(moduleItems(module), completed);
    const phase = roadmapPhases.find((item) => item.modules.includes(module.num));
    return h("a", { className: "module-card", href: "#/module/" + module.num },
      h("div", { className: "module-card-top" },
        h("div", { className: "module-number" }, "Module " + module.num),
        phase && h("span", null, phase.label)
      ),
      h("h2", null, moduleTitle(module)),
      h("p", null, desktop.goal || ""),
      h("div", { className: "topic-row" }, ((desktop.techs || []).slice(0, 4)).map(pill)),
      h("div", { className: "module-progress" },
        h("div", { className: "progress-row" },
          h("span", null, "Progress"),
          h("strong", null, progress.percent + "%")
        ),
        h("div", { className: "progress-bar" },
          h("div", { style: { width: progress.percent + "%" } })
        )
      ),
      h("div", { className: "module-card-foot" },
        h("span", null, progress.done + "/" + progress.total + " complete"),
        h("span", null, (module.projectVideos || []).length + " project videos")
      )
    );
  }

  function ModulePage({ id, completed, toggleCompleted }) {
    const module = data.modules.find((item) => item.num === id) || data.modules[0];
    const desktop = module.desktop || {};
    const [playing, setPlaying] = useState(null);
    const progress = progressFor(moduleItems(module), completed);
    const previousModule = moduleByNum(module.num - 1);
    const nextModule = moduleByNum(module.num + 1);
    const phase = roadmapPhases.find((item) => item.modules.includes(module.num));
    return h("main", { className: "page" },
      h("section", { className: "section detail-page" },
        h("a", { className: "back-link", href: "#/curriculum" }, "Back to curriculum"),
        h("article", { className: "course-detail" },
          h("div", { className: "detail-head" },
            h("div", { className: "module-number" }, "Module " + module.num),
            h("h1", null, moduleTitle(module)),
            h("div", { className: "detail-meta" },
              phase && h("span", null, phase.title),
              h("span", null, module.time),
              h("span", null, module.videos.length + " curated videos"),
              h("span", null, progress.done + "/" + progress.total + " completed")
            )
          ),
          h("div", { className: "detail-progress" },
            h("div", { className: "progress-row" },
              h("span", null, "Module progress"),
              h("strong", null, progress.percent + "%")
            ),
            h("div", { className: "progress-bar" },
              h("div", { style: { width: progress.percent + "%" } })
            )
          ),
          h("div", { className: "detail-body" },
            h("div", { className: "topic-row" }, (desktop.techs || []).map(pill)),
            h("div", { className: "module-sequence" },
              h("div", null,
                h("strong", null, "Prerequisite"),
                h("span", null, previousModule ? "Complete Module " + previousModule.num + ": " + moduleTitle(previousModule) : "Start here. This module has no prerequisite.")
              ),
              h("div", null,
                h("strong", null, "Next"),
                h("span", null, nextModule ? "Then continue to Module " + nextModule.num + ": " + moduleTitle(nextModule) : "Then continue to FDE Readiness.")
              )
            ),
            h("div", { className: "info-box quality-box" },
              h("strong", null, "Video-only learning rule: "),
              "watch the lessons below in order, then follow the project videos. The text on this page is only navigation and optional reference."
            ),
            h("h2", { className: "subheading" }, "YouTube Course Videos"),
            h("div", { className: "lesson-list" },
              module.videos.map((video) => h(Lesson, {
                key: video.id,
                video,
                itemKey: itemKey("module-" + module.num + "-lesson", video),
                completed,
                toggleCompleted,
                isPlaying: playing === video.id,
                setPlaying
              }))
            ),
            h("h2", { className: "subheading" }, "Project Walkthrough Videos"),
            h("p", { className: "module-note" },
              "Follow these after the lessons. I kept these practical and local-friendly where possible, so you do not need paid servers just to learn the module."
            ),
            h("div", { className: "lesson-list compact-lessons" },
              (module.projectVideos || []).map((video) => h(ProjectVideo, {
                key: video.url,
                video,
                itemKey: itemKey("module-" + module.num + "-project", video),
                completed,
                toggleCompleted,
                isPlaying: playing === youtubeId(video.url),
                setPlaying
              }))
            ),
            h("h2", { className: "subheading" }, "Build Targets"),
            h("div", { className: "project-grid" },
              h("div", { className: "info-box" }, h("strong", null, "Mini project"), h("p", null, desktop.miniProject || "")),
              h("div", { className: "info-box" }, h("strong", null, "Production project"), h("p", null, desktop.prodProject || ""))
            ),
            module.num === 20 && h("section", { className: "supplemental-track" },
              h("h2", { className: "subheading" }, "Portfolio Capstone Systems"),
              h("p", { className: "module-note" },
                "Use these as your final AI Engineer portfolio options. Build one deeply, then turn the rest into smaller demos or architecture writeups."
              ),
              h("div", { className: "capstone-grid" },
                (data.capstones || []).map((project) => h("div", { className: "capstone", key: project.id },
                  h("h2", null, project.title),
                  h("p", null, project.desc),
                  h("div", { className: "topic-row" }, (project.techs || []).map(pill))
                ))
              )
            ),
            (module.supplementalTracks || []).map((track) => h(SupplementalTrack, {
              key: track.id,
              module,
              track,
              completed,
              toggleCompleted
            })),
            module.num === 12 && data.claudeFdeTrack && h("section", { className: "supplemental-track" },
              h("h2", { className: "subheading" }, "Optional Track: Claude Code, Skills, MCP And Agent SDK"),
              h("p", { className: "module-note" },
                "This belongs here as AI engineering tooling after MCP and stateful agents. It is useful for delivery speed, code workflows, and tool integration, but it is not part of the core FDE path."
              ),
              h("div", { className: "topic-row" }, (data.claudeFdeTrack.coverage || []).map(pill)),
              h("div", { className: "lesson-list compact-lessons" },
                (data.claudeFdeTrack.videos || []).map((video) => h(ProjectVideo, {
                  key: video.url,
                  video: Object.assign({ cost: "Optional YouTube" }, video),
                  itemKey: itemKey("module-12-claude-tooling", video),
                  completed,
                  toggleCompleted,
                  isPlaying: playing === youtubeId(video.url),
                  setPlaying
                }))
              )
            ),
            h("details", { className: "optional-reference" },
              h("summary", null, "Optional reference after watching"),
              h("div", { className: "reference-grid" },
                h("div", { className: "info-box" },
                  h("strong", null, "GitHub repos"),
                  (desktop.repos || []).map((repo) => h("p", { key: repo.url },
                    h("a", { href: repo.url, target: "_blank", rel: "noreferrer" }, repo.name),
                    " - " + (repo.desc || "")
                  ))
                ),
                h("div", { className: "info-box" },
                  h("strong", null, "Official docs"),
                  (desktop.docs || []).map((doc) => h("p", { key: doc.url },
                    h("a", { href: doc.url, target: "_blank", rel: "noreferrer" }, doc.name)
                  ))
                )
              ),
              h("h2", { className: "subheading" }, "Checklist"),
              h("ul", { className: "checklist" }, (desktop.checklist || []).map((item) => h("li", { key: item }, item))),
              h("h2", { className: "subheading" }, "Interview Questions"),
              h("div", { className: "reference-grid" },
                (desktop.interviewQA || []).map((qa) => h("div", { className: "info-box", key: qa.q },
                  h("strong", null, qa.q),
                  h("p", null, qa.a)
                ))
              )
            )
            ,
            h("div", { className: "module-nav-actions" },
              previousModule && h("a", { className: "button", href: "#/module/" + previousModule.num }, "Previous Module"),
              nextModule
                ? h("a", { className: "button primary", href: "#/module/" + nextModule.num }, "Next Module")
                : h("a", { className: "button primary", href: "#/fde" }, "Continue To FDE Track")
            )
          )
        )
      )
    );
  }

  function CompleteButton({ complete, onClick }) {
    return h("button", {
      className: "complete-button" + (complete ? " complete" : ""),
      onClick
    }, complete ? "Completed" : "Mark complete");
  }

  function Lesson({ video, itemKey, completed, toggleCompleted, isPlaying, setPlaying }) {
    const complete = completed.has(itemKey);
    const skipText = String(video.skip || "").trim();
    const hasUsefulSkip = skipText && !/^none\.?$/i.test(skipText);
    return h("div", { className: "lesson-card" + (complete ? " is-complete" : "") },
      h("button", { className: "thumb", onClick: () => setPlaying(isPlaying ? null : video.id) },
        h("img", { src: "https://img.youtube.com/vi/" + video.id + "/mqdefault.jpg", alt: "" }),
        h("span", { className: "play" }, "▶")
      ),
      h("div", null,
        h("h3", null, video.order + ". " + video.title),
        h("div", { className: "lesson-meta" }, video.creator + " | " + video.duration + " | " + video.difficulty),
        h("p", { className: "lesson-copy" }, video.why),
        hasUsefulSkip && h("div", { className: "skip" }, "Skip: " + skipText),
        h("div", { className: "lesson-actions" },
          h("button", { className: "button primary", onClick: () => setPlaying(isPlaying ? null : video.id) }, isPlaying ? "Close Video" : "Play Here"),
          h("a", { className: "button", href: video.url, target: "_blank", rel: "noreferrer" }, "Open YouTube"),
          h(CompleteButton, { complete, onClick: () => toggleCompleted(itemKey) })
        ),
        isPlaying && h("div", { className: "video-frame" },
          h("iframe", {
            src: "https://www.youtube-nocookie.com/embed/" + video.id + "?autoplay=1&rel=0",
            title: video.title,
            allow: "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share",
            allowFullScreen: true
          })
        )
      )
    );
  }

  function ProjectVideo({ video, itemKey, completed, toggleCompleted, isPlaying, setPlaying }) {
    const id = youtubeId(video.url);
    const complete = itemKey && completed && completed.has(itemKey);
    return h("div", { className: "project-video-card" + (complete ? " is-complete" : "") },
      id && h("button", { className: "thumb small-thumb", onClick: () => setPlaying(isPlaying ? null : id) },
        h("img", { src: "https://img.youtube.com/vi/" + id + "/mqdefault.jpg", alt: "" }),
        h("span", { className: "play" }, "▶")
      ),
      h("div", null,
        h("h3", null, video.title),
        h("div", { className: "lesson-meta" }, video.creator + " | " + video.duration + " | " + video.cost),
        h("p", { className: "lesson-copy" }, video.why)
      ),
      h("div", { className: "lesson-actions" },
        id && h("button", { className: "button primary", onClick: () => setPlaying(isPlaying ? null : id) }, isPlaying ? "Close Video" : "Play Here"),
        h("a", { className: "button", href: video.url, target: "_blank", rel: "noreferrer" }, "Open YouTube"),
        itemKey && h(CompleteButton, { complete, onClick: () => toggleCompleted(itemKey) })
      ),
      isPlaying && id && h("div", { className: "video-frame" },
        h("iframe", {
          src: "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=1&rel=0",
          title: video.title,
          allow: "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share",
          allowFullScreen: true
        })
      )
    );
  }

  function SupplementalTrack({ module, track, completed, toggleCompleted }) {
    const [playing, setPlaying] = useState(null);
    return h("section", { className: "supplemental-track" },
      h("h2", { className: "subheading" }, "Additional Track: " + track.title),
      h("p", { className: "module-note" }, "Optional extra video track for this topic. Watch only if this tool matters for your job or capstone."),
      h("div", { className: "topic-row" }, (track.techs || []).map(pill)),
      h("div", { className: "lesson-list compact-lessons" },
        (track.videos || []).map((video) => h(ProjectVideo, {
          key: video.url,
          video: {
            title: video.step + ". " + video.title,
            creator: video.creator,
            url: video.url,
            duration: video.duration,
            cost: video.difficulty,
            why: video.whyBest
          },
          itemKey: itemKey("module-" + module.num + "-track-" + track.id, video),
          completed,
          toggleCompleted,
          isPlaying: playing === youtubeId(video.url),
          setPlaying
        }))
      ),
      h("div", { className: "project-grid" },
        h("div", { className: "info-box" }, h("strong", null, "Mini project"), h("p", null, track.miniProject || "")),
        h("div", { className: "info-box" }, h("strong", null, "Production project"), h("p", null, track.prodProject || ""))
      ),
      h("div", { className: "reference-grid" },
        h("div", { className: "info-box" },
          h("strong", null, "Repos"),
          (track.repos || []).map((repo) => h("p", { key: repo.url },
            h("a", { href: repo.url, target: "_blank", rel: "noreferrer" }, repo.name),
            " - " + (repo.desc || "")
          ))
        ),
        h("div", { className: "info-box" },
          h("strong", null, "Docs"),
          (track.docs || []).map((doc) => h("p", { key: doc.url },
            h("a", { href: doc.url, target: "_blank", rel: "noreferrer" }, doc.name)
          ))
        )
      )
    );
  }

  function CoursesPage({ completed }) {
    const specialtyTracks = [
      data.fdeCloudTrack,
      data.fdeAcademyTechnicalTrack,
      data.fdeAcademyConsultingTrack,
      data.claudeFdeTrack
    ].filter(Boolean);
    return h("main", { className: "page" },
      h("section", { className: "section page-hero" },
        h("div", { className: "eyebrow" }, "Course Catalog"),
        h("h1", null, "Courses And Tracks"),
        h("p", { className: "section-lead" },
          "Use this page to browse the curriculum by course area. For day-to-day study, follow the AI Engineer Path first, then the FDE Path."
        )
      ),
      h("section", { className: "section" },
        h("h2", { className: "section-title" }, "Main Learning Paths"),
        h("div", { className: "course-track-grid" },
          h("a", { className: "course-track-card primary-track", href: "#/" },
            h("div", { className: "phase-kicker" }, "Required"),
            h("h3", null, "AI Engineer Path"),
            h("p", null, "20 ordered modules covering APIs, RAG, agents, backend services, deployment, observability, guardrails, security, scaling, and capstone delivery."),
            h("span", null, "Start with Module 1")
          ),
          h("a", { className: "course-track-card", href: "#/fde" },
            h("div", { className: "phase-kicker" }, "After Core"),
            h("h3", null, "FDE Path"),
            h("p", null, "Cloud deployment, customer discovery, stakeholder communication, enterprise rollout, success metrics, and post-launch ownership."),
            h("span", null, "Start after Module 20")
          ),
          h("a", { className: "course-track-card", href: "#/projects" },
            h("div", { className: "phase-kicker" }, "Portfolio"),
            h("h3", null, "Production Projects"),
            h("p", null, "Capstone systems and portfolio ideas. Use these as proof that you can ship production-grade AI applications."),
            h("span", null, "Build alongside modules")
          )
        )
      ),
      h("section", { className: "section" },
        h("h2", { className: "section-title" }, "Core AI Engineering Modules"),
        h("div", { className: "module-grid" },
          data.modules.map((module) => h(ModuleCatalogCard, { module, completed, key: module.num }))
        )
      ),
      h("section", { className: "section" },
        h("h2", { className: "section-title" }, "Specialty And Optional Tracks"),
        h("div", { className: "course-track-grid" },
          specialtyTracks.map((track) => h("a", { className: "course-track-card", href: track === data.claudeFdeTrack ? "#/fde" : "#/fde", key: track.title },
            h("div", { className: "phase-kicker" }, track === data.claudeFdeTrack ? "Optional Tooling" : "FDE"),
            h("h3", null, track.title),
            h("p", null, track.subtitle),
            h("span", null, (track.videos || []).length + " videos")
          ))
        )
      )
    );
  }

  function ProjectsPage() {
    return h("main", { className: "page" },
      h("section", { className: "section page-hero" },
        h("div", { className: "eyebrow" }, "Portfolio Track"),
        h("h1", null, "Production Portfolio Systems"),
        h("p", { className: "section-lead" },
          "Build one deeply, then use the rest as smaller implementations or architecture writeups. These are the artifacts that make the AI Engineer/FDE transition credible."
        )
      ),
      h("section", { className: "section" },
        h("div", { className: "capstone-grid" },
          data.capstones.map((project) => h("div", { className: "capstone", key: project.id },
            h("h2", null, project.title),
            h("p", null, project.desc),
            h("div", { className: "topic-row" }, project.techs.map(pill))
          ))
        )
      )
    );
  }

  function FdeVideoTrack({ track, completed, toggleCompleted }) {
    const [playing, setPlaying] = useState(null);
    return h("section", { className: "section" },
      h("h2", { className: "section-title" }, track.title),
      h("p", { className: "section-lead" }, track.subtitle),
      h("div", { className: "topic-row" }, (track.coverage || []).map(pill)),
      h("div", { className: "lesson-list compact-lessons fde-track-list" },
        (track.videos || []).map((video) => h(ProjectVideo, {
          key: video.url,
          video: Object.assign({ cost: "Free YouTube" }, video),
          itemKey: itemKey("fde-" + slug(track.title), video),
          completed,
          toggleCompleted,
          isPlaying: playing === youtubeId(video.url),
          setPlaying
        }))
      )
    );
  }

  function FdeAcademyCoverage() {
    const statusClass = (status) => {
      if (/covered/i.test(status) && !/partial|light/i.test(status)) return "covered";
      if (/added/i.test(status)) return "added";
      if (/partial|light/i.test(status)) return "partial";
      return "";
    };
    return h("section", { className: "section" },
      h("h2", { className: "section-title" }, "FDE Academy Coverage Check"),
      h("p", { className: "section-lead" },
        "I compared your FDE Academy outline against this roadmap. Green items were already covered, blue items are newly added tracks, and amber items are intentionally lighter because your goal is AI application engineering, not model training or legal contracting."
      ),
      h("div", { className: "coverage-table" },
        (data.fdeAcademyCoverage || []).map((row) => h("div", { className: "coverage-row", key: row.area },
          h("div", null, h("strong", null, row.area), h("span", null, row.where)),
          h("span", { className: "coverage-status " + statusClass(row.status) }, row.status)
        ))
      )
    );
  }

  function FdeSkillLadder() {
    const ladder = [
      {
        step: "1",
        title: "Enterprise AI Foundation",
        goal: "Be able to build and explain production AI apps before entering customer environments.",
        learn: ["Python/FastAPI services", "OpenAI/Anthropic/Gemini APIs", "RAG fundamentals", "Vector search", "Agents and workflows"],
        route: "Complete Modules 1-12 first"
      },
      {
        step: "2",
        title: "Customer Discovery And Scoping",
        goal: "Turn an unclear business problem into a bounded AI use case, rollout plan, and success metric.",
        learn: ["Discovery interviews", "Problem diagnosis", "Use-case prioritization", "Business case", "Stakeholder communication"],
        route: "Watch the FDE Consulting And Delivery Track"
      },
      {
        step: "3",
        title: "Enterprise Data And Integration",
        goal: "Connect AI systems to customer data without breaking permissions, privacy, freshness, or ownership.",
        learn: ["PostgreSQL/Redis", "Documents and knowledge bases", "CRM/ticketing APIs", "SSO/RBAC", "PII and data retention"],
        route: "Modules 14, 16, 17, 18 plus the safety track"
      },
      {
        step: "4",
        title: "Cloud Deployment For Customers",
        goal: "Deploy the AI system inside a real enterprise cloud environment with secure model access.",
        learn: ["AWS Bedrock", "Azure AI Foundry", "Vertex AI", "IAM/VPC/private endpoints", "Docker/Kubernetes basics"],
        route: "Cloud FDE Track plus Modules 13 and 15"
      },
      {
        step: "5",
        title: "Production Operations",
        goal: "Own the system after launch: quality, cost, failures, regressions, monitoring, and support.",
        learn: ["LangSmith/Langfuse", "OpenTelemetry", "Grafana/Splunk/Dynatrace", "Evaluation", "Incident response", "Cost controls"],
        route: "Modules 11, 17, 18, 19 plus FDE Technical Stack Track"
      },
      {
        step: "6",
        title: "Adoption And Long-Term Ownership",
        goal: "Make customers actually use the system and trust it over time.",
        learn: ["User training", "Change management", "Executive demos", "Success reporting", "Feedback loops", "Support handoff"],
        route: "Consulting Track plus FDE readiness checklist"
      }
    ];
    return h("section", { className: "section" },
      h("h2", { className: "section-title" }, "FDE Learning Roadmap"),
      h("p", { className: "section-lead" },
        "Follow this order after the core AI Engineer path: customer discovery, enterprise integration, secure cloud deployment, production operations, and long-term adoption."
      ),
      h("div", { className: "skill-ladder" },
        ladder.map((item) => h("div", { className: "skill-step", key: item.step },
          h("div", { className: "skill-step-num" }, item.step),
          h("div", null,
            h("h3", null, item.title),
            h("p", null, item.goal),
            h("div", { className: "topic-row" }, item.learn.map(pill)),
            h("div", { className: "skill-route" }, item.route)
          )
        ))
      )
    );
  }

  function FdeRequiredCourses() {
    const courses = [
      {
        step: "1",
        title: "Prerequisite: Complete AI Engineer Roadmap",
        type: "Required prerequisite",
        goal: "Before FDE specialization, you need the full AI application engineering base: APIs, RAG, agents, backend services, evaluation, deployment, security, and architecture.",
        learn: ["Modules 1-20", "RAG", "Agents", "FastAPI", "Evaluation", "Security", "Capstone"],
        content: [
          ["Open AI Engineer Roadmap", "#/"],
          ["Modules 1-3: Python, model APIs, prompting, structured outputs, tool calling", "#/module/1"],
          ["Modules 4-7: embeddings, vector DBs, RAG, hybrid search, reranking", "#/module/4"],
          ["Modules 8-10: LangChain, LlamaIndex, LangGraph, MCP, agents, n8n", "#/module/8"],
          ["Modules 11-20: observability, FastAPI, Docker/K8s, cloud, auth, evals, guardrails, scaling, architecture, capstone", "#/module/11"]
        ]
      },
      {
        step: "2",
        title: "FDE Role, Customer Discovery And Solution Scoping",
        type: "FDE Course",
        goal: "Learn the FDE role and how to turn messy customer requests into a clear AI workflow, success metric, data map, and rollout plan.",
        learn: ["FDE role", "Discovery", "Diagnosis", "Business case", "Stakeholders", "Scoping workshop"],
        content: [
          ["FDE Consulting And Delivery Track videos below", null],
          ["Practice: write a one-page AI opportunity brief", null],
          ["Practice: convert a vague business request into scope, assumptions, risks, and success metrics", null]
        ]
      },
      {
        step: "3",
        title: "Backend Systems, Observability And Advanced Data Engineering",
        type: "FDE Course",
        goal: "Learn the brochure's backend/data layer: production services, observability, ingestion pipelines, freshness, and customer data movement into AI systems.",
        learn: ["Backend systems", "Observability", "Data ingestion", "Airflow-style pipelines", "Freshness", "Data quality"],
        content: [
          ["Module 11: observability and enterprise APM", "#/module/11"],
          ["Module 12: FastAPI product backend", "#/module/12"],
          ["Module 14: PostgreSQL, pgvector, Redis, semantic caching", "#/module/14"],
          ["FDE Product, Data And Multi-Tenant Delivery Track videos below", null],
          ["Practice: design a customer document ingestion pipeline with freshness, retries, backfills, and quality checks", null]
        ]
      },
      {
        step: "4",
        title: "AI-First Frontends With TypeScript And React",
        type: "FDE Course",
        goal: "Build customer-facing demos and product surfaces for AI workflows instead of leaving the AI system as only a backend API.",
        learn: ["React", "TypeScript", "AI chat UX", "RAG UI", "Auth-aware demos", "Customer workflow screens"],
        content: [
          ["FDE Product, Data And Multi-Tenant Delivery Track videos below", null],
          ["Practice: build a thin React/TypeScript customer demo over your RAG or agent API", null],
          ["Practice: add loading, streaming, citations, feedback, and escalation states", null]
        ]
      },
      {
        step: "5",
        title: "Enterprise Cloud AI Course",
        type: "FDE Course",
        goal: "Learn AWS-first deployment with enough Azure and Vertex literacy to work in real customer environments.",
        learn: ["AWS Bedrock", "AgentCore", "Azure AI Foundry", "Vertex AI", "IAM/VPC", "Private endpoints"],
        content: [
          ["Cloud FDE Track videos below", null],
          ["Module 15: Cloud AI Hyperscalers", "#/module/15"],
          ["Practice: design Bedrock/Azure/Vertex deployment options for the same RAG app", null]
        ]
      },
      {
        step: "6",
        title: "DevOps For AI Customer Deployment",
        type: "Core Modules",
        goal: "Learn how to package, deploy, run, and recover AI services with the operational discipline customers expect.",
        learn: ["Docker", "Kubernetes basics", "CI/CD mindset", "Workers", "Streaming", "Readiness probes", "Rollback"],
        content: [
          ["Module 13: Docker And Kubernetes For AI Deployment Basics", "#/module/13"],
          ["Module 16: Auth, streaming responses, background workers", "#/module/16"],
          ["Module 18: Production deployment, scaling, cost, security", "#/module/18"]
        ]
      },
      {
        step: "7",
        title: "FDE Lab Operations, Integration Harnesses And Live Debugging",
        type: "FDE Lab",
        goal: "Practice the field-engineering skills from the lab image: build integration harnesses, instrument services, debug live systems, validate pipelines, monitor drift, harden attack surfaces, and load test systems you did not build.",
        learn: ["Integration harness", "Contract tests", "Logs/metrics/traces", "CDC", "Reverse ETL", "Quality gates", "Drift", "Fine-tuning strategy", "Cross-account IAM", "Load testing"],
        content: [
          ["FDE Lab Operations Track videos below", null],
          ["Module 11: observability and enterprise APM", "#/module/11"],
          ["Module 13: deployment basics", "#/module/13"],
          ["Module 14: data tier and semantic caching", "#/module/14"],
          ["Module 18: guardrails, production deployment, security", "#/module/18"],
          ["Practice: create a test harness, seed data, mocked customer APIs, smoke tests, load tests, and a debug runbook for one AI integration", null]
        ]
      },
      {
        step: "8",
        title: "LLMOps / MLOps For AI Applications",
        type: "FDE Course",
        goal: "Learn how to trace, evaluate, monitor, debug, optimize, and improve LLM systems after they are deployed.",
        learn: ["LangSmith", "Langfuse", "OpenTelemetry", "Grafana", "Splunk", "Dynatrace", "Evals", "Drift", "Routing", "Cost attribution"],
        content: [
          ["Module 11: LLM observability plus enterprise APM with Grafana, Splunk, Dynatrace", "#/module/11"],
          ["Module 17: Evaluation and guardrails", "#/module/17"],
          ["FDE Technical Stack Track videos below: Helicone, W&B, Modal, BentoML, Together AI", null],
          ["Practice: design OpenTelemetry export from Langfuse/FastAPI into the customer's Grafana, Splunk, or Dynatrace stack", null]
        ]
      },
      {
        step: "9",
        title: "Enterprise Data, Security And Governance",
        type: "Core Modules",
        goal: "Learn how to connect enterprise data safely: databases, documents, permissions, PII, guardrails, and security controls.",
        learn: ["PostgreSQL", "Redis", "SSO/RBAC", "PII", "NeMo Guardrails", "OWASP LLM Top 10", "Data retention"],
        content: [
          ["Module 14: PostgreSQL, pgvector, Redis, semantic caching", "#/module/14"],
          ["Module 16: authentication, streaming, workers", "#/module/16"],
          ["Module 17: evaluation, guardrails, safety", "#/module/17"],
          ["Module 19: AI scaling, cost optimization, and security", "#/module/19"]
        ]
      },
      {
        step: "10",
        title: "Secure Enterprise Delivery And Incident Response",
        type: "FDE Course",
        goal: "Learn how to safely deliver AI systems into customer environments while handling permissions, audit trails, incidents, and escalation.",
        learn: ["Roles/permissions", "Audit trails", "Incident response", "Support escalation", "Customer deployment"],
        content: [
          ["Module 16: authentication, streaming, workers", "#/module/16"],
          ["Module 18: production ops, scaling, cost, security", "#/module/18"],
          ["Module 19: AI scaling, cost optimization, and security", "#/module/19"],
          ["FDE Enterprise Data, Multi-Tenant And HITL Delivery Track videos below", null],
          ["Practice: design audit logging, support access, permissions, and incident escalation for an enterprise AI rollout", null]
        ]
      },
      {
        step: "11",
        title: "AI Production Scaling, SRE And Reliability",
        type: "Core Modules",
        goal: "Learn how to keep AI systems fast, observable, secure, and affordable under real customer load.",
        learn: ["Caching", "Queues", "Rate limits", "Model routing", "SLO/SLI", "Alerting", "Runbooks", "Postmortems", "Cost optimization"],
        content: [
          ["Module 18: production ops, scaling, cost, security", "#/module/18"],
          ["Module 19: AI scaling, cost optimization, and security", "#/module/19"],
          ["SRE topics below: SLOs, dashboards, alerts, runbooks, incident response, postmortems, and support handoff", null]
        ]
      },
      {
        step: "12",
        title: "FDE Capstone: Discovery To Demo Day",
        type: "FDE Course",
        goal: "Run the brochure-style end-to-end customer engagement: discovery, solution design, secure build, demo, rollout plan, support handoff, and success metrics.",
        learn: ["Customer discovery", "Solution design", "Secure build", "Executive demo", "Rollout plan", "Support handoff"],
        content: [
          ["FDE Consulting And Delivery Track videos below", null],
          ["Module 20: enterprise capstone portfolio", "#/module/20"],
          ["Practice: create customer discovery notes, demo script, rollout plan, training notes, support handoff, and success report", null],
          ["FDE Readiness Checklist at the end of this page", null]
        ]
      }
    ];
    return h("section", { className: "section" },
      h("h2", { className: "section-title" }, "FDE Course Roadmap"),
      h("p", { className: "section-lead" },
        "Follow this order. Course 1 is the required AI Engineer foundation. The rest is the brochure and lab-aligned FDE specialization for your gaps: discovery, enterprise data engineering, cloud, DevOps, integration harnesses, live debugging, LLMOps, security/governance, SRE, reliability, and demo-day delivery."
      ),
      h("div", { className: "fde-course-list" },
        courses.map((course, index) => h("div", { className: "fde-course-item", key: course.title },
          h("div", { className: "skill-step-num" }, index + 1),
          h("div", null,
            h("div", { className: "phase-kicker" }, course.type),
            h("h3", null, course.title),
            h("p", null, course.goal),
            h("div", { className: "topic-row" }, course.learn.map(pill)),
            h("div", { className: "course-content-list" },
              h("strong", null, "Course content"),
              course.content.map(([label, href]) => href
                ? h("a", { href, key: label }, label)
                : h("span", { key: label }, label)
              )
            )
          )
        ))
      )
    );
  }

  function FdePage({ completed, toggleCompleted }) {
    const missing = [
      "Product sense and user discovery",
      "SSO/RBAC and enterprise identity",
      "Data governance, legal, privacy, and retention",
      "Customer-specific evaluation datasets",
      "Advanced data engineering for ingestion freshness, retries, backfills, and quality checks",
      "Production-grade integration harnesses, contract tests, mocks, and smoke tests",
      "Production debugging with logs, metrics, traces, rollback plans, and live-system pressure",
      "CDC, webhooks, reverse ETL, batch vs streaming, and data quality gates",
      "MLOps drift/model decay monitoring and fine-tuning strategy for client use cases",
      "Enterprise LLM routing, prompt caching, speed, and cost optimization",
      "Enterprise permissions, audit trails, and support access",
      "Cross-account access, attack-surface review, compliance/privacy, security audit, and hardening",
      "Performance engineering and load testing for systems you did not build",
      "Prompt/version release management",
      "Stakeholder demos and executive communication",
      "SLOs, alerts, runbooks, incident response, postmortems, and support handoff",
      "Commercial awareness: ROI, cost, adoption, and success metrics"
    ];
    return h("main", { className: "page" },
      h("section", { className: "section page-hero" },
        h("div", { className: "eyebrow" }, "Forward Deployed Engineer Layer"),
        h("h1", null, "FDE Readiness: Cloud Deployment + Customer Delivery"),
        h("p", { className: "section-lead" },
          "The technical curriculum is strong for AI application engineering. To become a Forward Deployed Engineer, add AWS-first enterprise deployment, customer discovery, governance, stakeholder communication, and post-launch ownership."
        ),
        h("div", { className: "path-switcher" },
          h("a", { className: "path-card", href: "#/" },
            h("strong", null, "AI Engineer Path"),
            h("span", null, "Complete this first")
          ),
          h("a", { className: "path-card active-path", href: "#/fde" },
            h("strong", null, "FDE Path"),
            h("span", null, "Customer deployment layer")
          )
        )
      ),
      h(HowToUsePath, { mode: "fde" }),
      h(FdeAcademyCoverage),
      h(FdeRequiredCourses),
      h("section", { className: "section compact-section" },
        h("h2", { className: "section-title" }, "FDE Video Courses"),
        h("p", { className: "section-lead" },
          "These are the YouTube courses for the FDE specialization. Watch them in this order: consulting and discovery first, cloud deployment second, then technical platform and LLMOps."
        )
      ),
      data.fdeAcademyConsultingTrack && h(FdeVideoTrack, { track: data.fdeAcademyConsultingTrack, completed, toggleCompleted }),
      fdeEnterpriseDeliveryTrack() && h(FdeVideoTrack, { track: fdeEnterpriseDeliveryTrack(), completed, toggleCompleted }),
      data.fdeCloudTrack && h(FdeVideoTrack, { track: data.fdeCloudTrack, completed, toggleCompleted }),
      h(FdeVideoTrack, { track: awsOperationsTrack(), completed, toggleCompleted }),
      h(FdeVideoTrack, { track: enterpriseApmTrack(), completed, toggleCompleted }),
      data.fdeLabOperationsTrack && h(FdeVideoTrack, { track: data.fdeLabOperationsTrack, completed, toggleCompleted }),
      data.fdeAcademyTechnicalTrack && h(FdeVideoTrack, { track: data.fdeAcademyTechnicalTrack, completed, toggleCompleted }),
      h("section", { className: "section compact-section" },
        h("h2", { className: "section-title" }, "SRE And Production Ownership Topics"),
        h("p", { className: "section-lead" },
          "Learn these after deployment basics. They matter when an FDE has to keep a customer-facing AI system reliable, observable, secure, and affordable after launch."
        ),
        h("div", { className: "outcomes" },
          fdeScalingTopics().map((topic) => h("div", { className: "outcome", key: topic.title },
            h("h3", null, topic.title),
            h("p", null, topic.why)
          ))
        )
      ),
      h("section", { className: "section" },
        h("h2", { className: "section-title" }, "FDE Readiness Checklist"),
        h("div", { className: "course-detail" },
          h("div", { className: "detail-body" },
            h("ul", { className: "checklist" }, missing.map((item) => h("li", { key: item }, item)))
          )
        )
      )
    );
  }

  ReactDOM.createRoot(document.getElementById("root")).render(h(App));
})();
