"""
Prompt generator — mirrors the logic in the web app's lib/prompt-generator.ts
"""

MODE_ROLES = {
    "write": "an expert content writer with deep experience crafting clear, compelling, and audience-appropriate writing",
    "code": "a senior software engineer with 10+ years of experience building production systems, writing clean and maintainable code",
    "analyze": "a seasoned analyst who excels at breaking down complex information, identifying patterns, and delivering actionable insights",
    "debug": "a senior debugging specialist who methodically traces root causes, tests hypotheses, and delivers precise fixes with clear reasoning",
    "learn": "an experienced educator who explains complex topics using clear analogies, progressive examples, and builds understanding step by step",
    "brainstorm": "a creative strategist who generates innovative ideas from multiple angles, evaluates feasibility, and prioritizes by impact",
}

MODE_INSTRUCTIONS = {
    "write": [
        "Match the requested tone precisely throughout.",
        "Open with a compelling hook that establishes value immediately.",
        "Structure content with clear progression — each section builds on the previous.",
        "Use concrete examples and specific details rather than vague generalizations.",
        "End with a clear conclusion or call-to-action.",
    ],
    "code": [
        "Write clean, well-structured code following established conventions.",
        "Handle edge cases and error scenarios explicitly.",
        "Include meaningful comments only where the logic isn't self-evident.",
        "Consider performance, security, and maintainability implications.",
        "Explain key design decisions and trade-offs you made.",
    ],
    "analyze": [
        "Start with a high-level summary of key findings before diving into details.",
        "Support every conclusion with specific evidence or data points.",
        "Identify patterns, anomalies, and non-obvious connections.",
        "Clearly distinguish between facts, inferences, and assumptions.",
        "Conclude with prioritized, actionable recommendations.",
    ],
    "debug": [
        "Start by reproducing and clearly describing the problem.",
        "Systematically trace the root cause — show your reasoning at each step.",
        "Identify whether this is a symptom of a deeper issue.",
        "Provide the fix with clear before/after comparison.",
        "Suggest preventive measures to avoid recurrence.",
    ],
    "learn": [
        "Start with the simplest mental model before adding complexity.",
        "Use relatable analogies that map to concepts the learner already knows.",
        "Include concrete, runnable examples at each level of complexity.",
        "Highlight common misconceptions and explain why they're wrong.",
        "Provide a clear learning path for going deeper.",
    ],
    "brainstorm": [
        "Generate ideas across multiple dimensions — don't cluster in one area.",
        "Push beyond obvious first-thought ideas into creative, non-obvious territory.",
        "For each idea, briefly note feasibility, impact, and effort.",
        "Identify ideas that could be combined for compounding value.",
        "Rank final suggestions by impact-to-effort ratio.",
    ],
}

LENGTH_MAP = {
    "Brief": "keep it concise and to the point",
    "Medium": "provide a moderate level of detail",
    "Detailed": "be thorough and detailed in your response",
    "Comprehensive": "be exhaustive and cover every relevant angle",
    "As needed": "adjust the length to what the content requires",
}

FORMAT_MAP = {
    "Paragraphs": "Format your response as well-structured paragraphs with clear topic sentences.",
    "Bullet Points": "Format your response as organized bullet points, grouped by theme where appropriate.",
    "Step-by-Step": "Format your response as numbered steps, each with a clear action and expected outcome.",
    "Table": "Format your response as a structured table with clear column headers and organized rows.",
    "Code Block": "Format your response as clean, well-commented code blocks with explanations.",
    "JSON": "Format your response as well-structured JSON with descriptive keys.",
    "Markdown": "Format your response in clean Markdown with proper headings, lists, and emphasis.",
    "XML": "Format your response using XML tags to clearly structure and separate each section.",
}

EXTRA_MAP = {
    "Include examples": "Include concrete, specific examples to illustrate key points.",
    "Suggest alternatives": "Suggest alternative approaches and explain when each would be preferred.",
    "Think step-by-step": "Think through this step-by-step, showing your reasoning at each stage.",
    "Pros & cons": "Evaluate pros and cons for each option or approach discussed.",
    "Cite sources": "Cite sources or references where applicable.",
    "No filler / no fluff": "Be direct — no filler phrases, hedging, or unnecessary preamble.",
    "Be critical / honest": "Be critically honest — flag weaknesses, risks, and things that could go wrong.",
    "Actionable output": "Make every recommendation immediately actionable with clear next steps.",
    "Include code snippets": "Include relevant code snippets with inline explanations.",
    "Compare approaches": "Compare different approaches side-by-side with trade-offs for each.",
}


def generate_prompt(state: dict) -> str:
    sections = []

    # Role
    custom_role = (state.get("role") or "").strip()
    if custom_role:
        sections.append(f"You are {custom_role}.")
    elif state.get("mode"):
        default_role = MODE_ROLES.get(state["mode"])
        if default_role:
            sections.append(f"You are {default_role}.")

    # Context
    if (state.get("context") or "").strip():
        sections.append(state["context"].strip())

    # Task
    sections.append(state["task"].strip())

    # Mode instructions
    if state.get("mode"):
        instructions = MODE_INSTRUCTIONS.get(state["mode"], [])
        if instructions:
            sections.append(" ".join(instructions))

    # Constraints
    parts = []
    if state.get("tone"):
        parts.append(f"Use a {state['tone'].lower()} tone")
    if state.get("audience"):
        parts.append(f"target the response for a {state['audience'].lower()} audience")
    if state.get("length") and state["length"] in LENGTH_MAP:
        parts.append(LENGTH_MAP[state["length"]])
    if parts:
        constraint = ", ".join(parts)
        sections.append(constraint[0].upper() + constraint[1:] + ".")

    # Format
    if state.get("output_format") and state["output_format"] in FORMAT_MAP:
        sections.append(FORMAT_MAP[state["output_format"]])

    # Extras
    extras = state.get("extras") or []
    extra_sentences = [EXTRA_MAP[e] for e in extras if e in EXTRA_MAP]
    if extra_sentences:
        sections.append(" ".join(extra_sentences))

    # Avoid
    if (state.get("avoid") or "").strip():
        sections.append(f"Avoid: {state['avoid'].strip()}.")

    return "\n\n".join(sections)
