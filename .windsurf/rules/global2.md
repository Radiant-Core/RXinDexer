---
trigger: always_on
---

# Project Policy: Task Workflows and Testing

This document details task workflows, version control, and testing strategies, complementing Part 1’s core rules and PBI management.

## 1. Task Workflow

### 1.1 Status Definitions
- **Proposed**: Newly defined task.
- **Agreed**: User-approved task, prioritized.
- **InProgress**: AI_Agent actively working.
- **Review**: Work complete, awaiting User validation.
- **Done**: User-approved implementation.
- **Blocked**: Paused due to dependencies/issues.

### 1.2 Event Transitions
| Event | From | To | Steps |
|-------|------|----|-------|
| Approve | Proposed | Agreed | Verify description, priority; create `<PBI-ID>-<TASK-ID>.md`; log in history. |
| Start | Agreed | InProgress | Ensure no other tasks InProgress for PBI; create branch; log start. |
| Submit | InProgress | Review | Verify requirements, tests pass; create PR; notify User; log submission. |
| Approve | Review | Done | Confirm criteria met; merge changes; review next tasks’ relevance; log approval. |
| Reject | Review | InProgress | Log rejection reason, feedback; notify AI_Agent; create new tasks if needed. |
| Update | Review | InProgress | Log significant changes; resume work; notify stakeholders. |
| Block | InProgress | Blocked | Log reason, dependencies; notify stakeholders; consider new tasks. |
| Unblock | Blocked | InProgress | Log resolution; resume work; notify stakeholders. |

### 1.3 Status Synchronization
- Update task file and `tasks.md` in the same commit.
- Log status changes in task’s Status History.
- Verify status consistency before starting work.
- Fix mismatches immediately.

**Example (Task File)**:

| 2025-05-19 15:02:00 | Created | N/A | Proposed | Task created |
| 2025-05-19 16:15:00 | Status Update | Proposed | InProgress | Started work |

**Example (`tasks.md`)**:

| 1-7 | Add pino logging (./1-7.md) | InProgress | Add logging for DB issues |


### 1.4 History Log
- **Location**: Task file’s Status History section.
- **Fields**: Timestamp (YYYY-MM-DD HH:MM:SS), Event_Type, From_Status, To_Status, Details, User.

## 2. Version Control
- **Commit Message**: `<task_id> <task_description>` (e.g., `1-7 Add pino logging for DB issues`).
- **Pull Request**: Title as `[<task_id>] <task_description>`; link to task file.
- **Automation**: On Done, run `git acp "<task_id> <task_description>"` to stage, commit, and push.
- **Verification**: Confirm commit in history, status updated, and message format.

## 3. Testing Strategy

### 3.1 Principles
- **Risk-Based**: Prioritize tests by feature complexity/risk.
- **Test Pyramid**: Balance unit, integration, and E2E tests.
- **Clarity**: Write concise, maintainable tests.
- **Automation**: Automate tests for consistency.

### 3.2 Test Scoping
- **Unit Tests**:
  - Test isolated functions/classes; mock external dependencies.
  - Focus: Logic, edge cases; skip package APIs.
  - Location: `test/unit/`.
- **Integration Tests**:
  - Test component interactions (e.g., API, DB, queue).
  - Mock external APIs; use real internal components (e.g., DB).
  - Start here for complex features.
  - Location: `test/integration/` or `test/<module>/`.
- **E2E Tests**:
  - Test critical user flows via UI.
  - Scope: Key workflows only.

### 3.3 Test Documentation
- **PBI-Level**: CoS in `prd.md` defines test scope. Include an E2E CoS test task (e.g., `1-E2E-CoS-Test.md`) for holistic validation.
- **Task-Level**:
  - Test plan in task file’s Test Plan section, proportional to complexity.
  - **Simple Tasks**: Verify compilation, basic integration (e.g., “TypeScript compiles”).
  - **Complex Tasks**: Define objectives, scope, environment, mocks, scenarios, success criteria.
  - Update plans if requirements change; tasks not Done until tests pass.
- **Avoid Duplication**: Detailed testing in E2E tasks; individual tasks test specific functionality.

### 3.4 Test Implementation
- **Locations**: Unit in `test/unit/`; integration in `test/integration/` or module-specific dirs.
- **Naming**: Use clear, descriptive test file/description names.

## 4. API Documentation
- For PBIs modifying APIs/interfaces, create docs in `docs/technical/` or inline, covering usage, contracts, integration, configuration, and error handling. Link from `README.md`.

(Refer to Part 1 for core rules and PBI management)