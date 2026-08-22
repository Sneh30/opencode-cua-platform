/**
 * Task Orchestration Manager — persistent task queue with checkpointing/resume.
 *
 * Survives control server restarts, tracks long-running tasks, enables
 * resume from failure points.
 */

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const TASKS_FILE = path.join(__dirname, "..", "..", "logs", "tasks.json");

class TaskManager {
  constructor() {
    this.tasks = new Map(); // taskId -> task object
    this.load();
    // Auto-save every 5s
    this.saveInterval = setInterval(() => this.save(), 5000);
    this.saveInterval.unref();
  }

  load() {
    try {
      if (fs.existsSync(TASKS_FILE)) {
        const data = JSON.parse(fs.readFileSync(TASKS_FILE, "utf8"));
        for (const t of data.tasks || []) {
          this.tasks.set(t.id, t);
        }
        console.log(`[task-manager] Loaded ${this.tasks.size} tasks from disk`);
      }
    } catch (e) {
      console.error("[task-manager] Failed to load tasks:", e.message);
    }
  }

  save() {
    try {
      const data = {
        version: 1,
        savedAt: Date.now(),
        tasks: Array.from(this.tasks.values()),
      };
      fs.writeFileSync(TASKS_FILE + ".tmp", JSON.stringify(data, null, 2));
      fs.renameSync(TASKS_FILE + ".tmp", TASKS_FILE);
    } catch (e) {
      console.error("[task-manager] Failed to save tasks:", e.message);
    }
  }

  // Create a new task
  create({ text, model = null, priority = "normal", metadata = {} }) {
    const id = "task_" + crypto.randomBytes(8).toString("hex");
    const task = {
      id,
      text,
      model,
      priority,
      metadata,
      status: "pending", // pending, running, checkpointed, completed, failed
      createdAt: Date.now(),
      updatedAt: Date.now(),
      startedAt: null,
      completedAt: null,
      checkpoints: [],
      currentStep: 0,
      totalSteps: 0,
      progress: 0,
      result: null,
      error: null,
      sessionId: null,
      retryCount: 0,
      maxRetries: 3,
    };
    this.tasks.set(id, task);
    this.save();
    return task;
  }

  // Get task by ID
  get(id) {
    return this.tasks.get(id) || null;
  }

  // List tasks with optional filter
  list({ status = null, limit = 100 } = {}) {
    let tasks = Array.from(this.tasks.values());
    if (status) tasks = tasks.filter(t => t.status === status);
    tasks.sort((a, b) => b.createdAt - a.createdAt);
    return tasks.slice(0, limit);
  }

  // Mark task as running (assigned to session)
  start(taskId, sessionId) {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    task.status = "running";
    task.sessionId = sessionId;
    task.startedAt = Date.now();
    task.updatedAt = Date.now();
    this.save();
    return true;
  }

  // Add a checkpoint (milestone)
  checkpoint(taskId, { step, description, artifacts = [] }) {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    task.checkpoints.push({
      step,
      description,
      artifacts,
      timestamp: Date.now(),
    });
    task.currentStep = step;
    task.progress = task.totalSteps > 0 ? Math.round((step / task.totalSteps) * 100) : 0;
    task.updatedAt = Date.now();
    task.status = "checkpointed";
    this.save();
    return true;
  }

  // Update progress
  progress(taskId, { step, totalSteps, description }) {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    if (totalSteps !== undefined) task.totalSteps = totalSteps;
    if (step !== undefined) task.currentStep = step;
    if (description) task.checkpoints.push({ step: task.currentStep, description, timestamp: Date.now() });
    task.progress = task.totalSteps > 0 ? Math.round((task.currentStep / task.totalSteps) * 100) : 0;
    task.updatedAt = Date.now();
    this.save();
    return true;
  }

  // Complete task successfully
  complete(taskId, result) {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    task.status = "completed";
    task.result = result;
    task.completedAt = Date.now();
    task.updatedAt = Date.now();
    task.progress = 100;
    this.save();
    return true;
  }

  // Fail task
  fail(taskId, error) {
    const task = this.tasks.get(taskId);
    if (!task) return false;
    task.status = "failed";
    task.error = error;
    task.completedAt = Date.now();
    task.updatedAt = Date.now();
    this.save();
    return true;
  }

  // Retry a failed task (reset for re-execution)
  retry(taskId) {
    const task = this.tasks.get(taskId);
    if (!task || task.status !== "failed") return false;
    task.retryCount++;
    if (task.retryCount > task.maxRetries) return false;
    task.status = "pending";
    task.error = null;
    task.sessionId = null;
    task.startedAt = null;
    task.completedAt = null;
    task.updatedAt = Date.now();
    this.save();
    return true;
  }

  // Get next pending task for execution
  getNextPending() {
    const pending = Array.from(this.tasks.values())
      .filter(t => t.status === "pending")
      .sort((a, b) => {
        // Priority: high > normal > low, then FIFO
        const prio = { high: 3, normal: 2, low: 1 };
        return (prio[b.priority] || 2) - (prio[a.priority] || 2) || a.createdAt - b.createdAt;
      });
    return pending[0] || null;
  }

  // Get tasks that can be resumed (checkpointed but not completed)
  getResumable() {
    return Array.from(this.tasks.values())
      .filter(t => t.status === "checkpointed" || (t.status === "running" && t.sessionId));
  }

  // Resume from last checkpoint
  resume(taskId) {
    const task = this.tasks.get(taskId);
    if (!task) return null;
    if (task.status !== "checkpointed" && task.status !== "running") return null;
    // Return the last checkpoint data for the executor to continue from
    const lastCp = task.checkpoints[task.checkpoints.length - 1];
    return {
      taskId: task.id,
      text: task.text,
      model: task.model,
      lastCheckpoint: lastCp,
      checkpoints: task.checkpoints,
    };
  }

  // Cleanup old completed/failed tasks (keep last N)
  prune(keep = 200) {
    const completed = Array.from(this.tasks.values())
      .filter(t => t.status === "completed" || t.status === "failed")
      .sort((a, b) => (b.completedAt || 0) - (a.completedAt || 0));
    if (completed.length > keep) {
      for (const t of completed.slice(keep)) {
        this.tasks.delete(t.id);
      }
      this.save();
    }
  }

  shutdown() {
    if (this.saveInterval) clearInterval(this.saveInterval);
    this.save();
  }
}

module.exports = { TaskManager, TASKS_FILE };