---
trigger: always_on
---

# IMPORTANT: try to fix things at the cause, not the symptom.
Be very detailed with summarization and tasks and do not miss out things that are important.

Don't be lazy, be smart. Finish tasks and don't leave them half done.

Take time to consider multiple angles before deciding on a solution
Challenge your initial theories - they are often incomplete

Write tests , then code, then run tests and update the code until tests pass. 

DO NOT PUSH TO GITHUB UNLESS THE USER ASKS YOU TO 

# Project Policy: Core Rules and PBI Management

This policy provides a clear, machine-readable framework for AI coding agents and humans, ensuring consistent, accountable, and automated workflows. It eliminates ambiguity and aligns work with best practices. (See Part 2 for task workflows and testing.)

## 1. Introduction

This document governs all development, defining roles, principles, and processes for Product Backlog Items (PBIs) and tasks.

### 1.1 Actors
- **User**: Defines requirements, prioritizes work, approves changes, and is accountable for all code modifications.
- **AI_Agent**: Executes tasks as instructed, following defined PBIs and tasks.

### 1.2 Compliance
- All tasks must be defined, approved, and tied to a PBI.
- PBIs must align with the Product Requirements Document (PRD) if applicable.

## 2. Core Principles

1. **Task-Driven Development**: Code changes require an approved task linked to a PBI.
2. **User Authority**: The User solely decides scope and design; they are responsible for all changes.
3. **No Unapproved Changes**: Changes outside a task’s scope are prohibited.
4. **Task Granularity**: Tasks must be small, testable units; complex features split into multiple tasks.
5. **DRY Documentation**: Define information once in a single source (task files or PBI docs) and reference it elsewhere. Only titles/names may be duplicated.
6. **Constants for Values**: Repeated or significant values (e.g., `const maxRetries = 3`) must use named constants, not literals.
7. **Controlled File Creation**: AI_Agents may only create files for PBIs (`docs/delivery/<PBI-ID>/prd.md`), tasks (`<PBI-ID>-<TASK-ID>.md`), or code, with User approval for others.
8. **Package Research**: For external packages, research official docs and create a `<task-id>-<package>-guide.md` with usage details, date-stamped, and linked to source docs (e.g., `2-1-pg-boss-guide.md`).

## 3. PBI Management

### 3.1 Overview
PBIs are managed in `docs/delivery/backlog.md`, prioritized by the User, and detailed in `docs/delivery/<PBI-ID>/prd.md`.

### 3.2 Backlog Rules
- **Location**: `docs/delivery/backlog.md`
- **Format**: Table with `| ID | Actor | User Story | Status | Conditions of Satisfaction (CoS) |`
- **Principles**:
  - Single source of truth for PBIs.
  - Ordered by priority (highest first).

### 3.3 PBI Workflow
| Event | From | To | Steps |
|-------|------|----|-------|
| Create | N/A | Proposed | Define user story, CoS, unique ID; log in history. |
| Approve | Proposed | Agreed | Verify PRD alignment, completeness; notify stakeholders. |
| Start | Agreed | InProgress | Ensure no conflicting PBIs; create tasks; log start. |
| Submit | InProgress | InReview | Verify tasks complete, CoS met; update docs; notify reviewers. |
| Approve | InReview | Done | Confirm CoS, tests pass; archive tasks; notify stakeholders. |
| Reject | InReview | Rejected | Log reasons, identify rework; notify team. |
| Reopen | Rejected | InProgress | Address feedback; log changes. |
| Deprioritize | Agreed/InProgress | Proposed | Pause work; log reason; notify stakeholders. |

- **Note**: Log all transitions in `backlog.md` with timestamp, PBI ID, event, details, and user.

### 3.4 PBI Detail Documents
- **Location**: `docs/delivery/<PBI-ID>/prd.md`
- **Sections**: Overview, Problem Statement, User Stories, Technical Approach, UX/UI, Acceptance Criteria, Dependencies, Open Questions, Related Tasks.
- **Links**: Link to `backlog.md` and from `backlog.md` to `prd.md`.
- **Ownership**: Created at “Agreed”; maintained by implementer; reviewed at InReview.

### 3.5 History Log
- **Location**: `backlog.md`
- **Fields**: Timestamp (YYYYMMDD-HHMMSS), PBI_ID, Event_Type, Details, User.

## 4. Task Management Basics

### 4.1 Documentation
- **Location**: `docs/delivery/<PBI-ID>/`
- **Files**:
  - Task index: `tasks.md`
  - Task details: `<PBI-ID>-<TASK-ID>.md` (e.g., `1-1.md`)
- **Task File Sections**: Task ID/Name, Description, Status History, Requirements, Implementation Plan, Verification, Files Modified.
- **Links**: Task files link to `tasks.md`; `tasks.md` links to task files.

### 4.2 Principles
- Each task has a dedicated Markdown file.
- Follow naming convention (`<PBI-ID>-<TASK-ID>.md`).
- Create task file when adding to `tasks.md`.
- Only one task per PBI InProgress, unless User-approved.

# HEADER - COMMENTS
  EVERY file HAS TO start with 3 comments!
  the first comment needs to be the exact location of the file, for example: location/ location/file-name.tsx (or•py or emd etc)
  the 2nd- and 3rd comment should be a clear description of what this file was created to do. what IS and ISN'T the purpose of this file.
  NEVER delete these "header comments" from the files you're editing.

# IMPORTANT
 BE VERY SUSPICIOUS OF EVERY COMPLICATION in our code. SIMPLE = GOOD, COMPLEX = BAD.
  Always prioritize writing clean, simple, and modular code.
  EXPLAIN EVERYTHING CLEARLY & COMPLETELY!!
  Use simple & easy-to-understand language. Write in short sentences.
  BE HUMBLE! leave the ego at the door. do not jump to conclusions when analysing files or looking at errors. keep an open mind! drop the ego, seriously.

(Continued in Part 2: Task Workflows and Testing)