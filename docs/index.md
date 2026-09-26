---
title: Sphragis
description: A pre-registered test of whether an organization's house style is learnable from the code it reviews.
hide:
  - navigation
  - toc
  - footer
---

<div class="hero" markdown>

# Sphragis

**Can a model learn an organization's house style from the code it reviews?**
{ .hero-subtitle }

<div class="hero-buttons" markdown>

[:octicons-law-24: The protocol](protocol.md){ .md-button .md-button--primary }
[:octicons-checklist-24: Registered decisions](registered-decisions.md){ .md-button }

</div>

<div class="hero-tagline" markdown>

:octicons-lock-24: Sealed test window | :octicons-git-pull-request-24: Public code review | :octicons-mortar-board-24: MSR 2027 Registered Report
{ .hero-modes }

</div>

</div>

<div class="scroll-hint" aria-hidden="true">
  <div class="scroll-chevron"></div>
</div>

<section class="landing-section landing-section--intro">
  <div class="section-inner">
    <h2 class="section-title">What is Sphragis?</h2>
    <p class="section-lead">Every organization has conventions its reviewers enforce and no public model has seen. Sphragis tests whether a model can learn them, as a registered report: the question, the pass rule and the checks are fixed before the confirmatory data is collected.</p>
  </div>
</section>

<section class="landing-section">
  <div class="section-inner">
    <h2 class="section-title">From review to verdict</h2>
    <div class="pipeline-flow">
      <div class="pipeline-step">
        <span class="step-icon material-symbols-outlined">rate_review</span>
        <span class="step-label">Mine</span>
      </div>
      <div class="pipeline-step">
        <span class="step-icon material-symbols-outlined">person_off</span>
        <span class="step-label">Scrub</span>
      </div>
      <div class="pipeline-step">
        <span class="step-icon material-symbols-outlined">ac_unit</span>
        <span class="step-label">Freeze</span>
      </div>
      <div class="pipeline-step">
        <span class="step-icon material-symbols-outlined">lock</span>
        <span class="step-label">Seal</span>
      </div>
      <div class="pipeline-step">
        <span class="step-icon material-symbols-outlined">model_training</span>
        <span class="step-label">Train</span>
      </div>
      <div class="pipeline-step">
        <span class="step-icon material-symbols-outlined">gavel</span>
        <span class="step-label">Gate</span>
      </div>
    </div>
    <p class="pipeline-caption">Review threads &rarr; pseudonymised examples &rarr; frozen windows &rarr; a sealed test &rarr; adapters &rarr; a coded pass rule</p>
  </div>
</section>

<section class="landing-section">
  <div class="section-inner">
    <h2 class="section-title">Explore</h2>
    <div class="feature-grid">
      <a href="protocol/" class="feature-card" style="--card-accent: #b3261e">
        <span class="feature-icon material-symbols-outlined">description</span>
        <div class="feature-name">Protocol</div>
        <p>The question, the gate, the corpus and where it stands.</p>
      </a>
      <a href="registered-decisions/" class="feature-card" style="--card-accent: #c93a26">
        <span class="feature-icon material-symbols-outlined">fact_check</span>
        <div class="feature-name">Registered decisions</div>
        <p>Every choice fixed before the seal opens, and the evidence behind it.</p>
      </a>
      <a href="outcome-neutral/" class="feature-card" style="--card-accent: #e0533f">
        <span class="feature-icon material-symbols-outlined">verified</span>
        <div class="feature-name">Outcome-neutral tests</div>
        <p>What has to hold before any result can be read.</p>
      </a>
      <a href="artifacts/" class="feature-card" style="--card-accent: #e8735a">
        <span class="feature-icon material-symbols-outlined">inventory_2</span>
        <div class="feature-name">Artifact index</div>
        <p>Every committed measurement, and the script that wrote it.</p>
      </a>
      <a href="https://github.com/ajbarea/sphragis/blob/main/docs/research-log.md" class="feature-card" style="--card-accent: #f0917a">
        <span class="feature-icon material-symbols-outlined">history_edu</span>
        <div class="feature-name">Research log</div>
        <p>The dated record, on GitHub, including every withdrawn reading.</p>
      </a>
    </div>
  </div>
</section>

<footer class="landing-footer">
  <span>2026 AJ Barea</span>
  <a href="https://github.com/ajbarea/sphragis" aria-label="Sphragis on GitHub">
    <img src="assets/github.svg" alt="" width="18" height="18">
  </a>
</footer>
