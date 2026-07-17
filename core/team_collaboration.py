"""
Командная работа и управление проектами
Автоматизация совместной работы, управление проектами, анализ коммуникаций
"""

import json
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime

import logging

_logger = logging.getLogger(__name__)


class TeamCollaborationEngine:
    """
    Движок командной работы и управления проектами
    Автоматизирует совместную работу и анализирует коммуникации
    """

    def __init__(self, base_dir: Path):
        self.team_path = base_dir / "config" / "team_data.json"
        self.team_data = self._load_team_data()

    def _load_team_data(self) -> Dict:
        """Загружает данные командной работы"""
        if self.team_path.exists():
            try:
                with open(self.team_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

        # Данные по умолчанию
        return {
            "team_members": [],
            "projects": [],
            "meetings": [],
            "tasks": [],
            "communication_patterns": {
                "frequent_contacts": [],
                "response_times": {},
                "meeting_frequency": {}
            }
        }

    def _save_team_data(self):
        """Сохраняет данные командной работы"""
        try:
            self.team_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.team_path, "w", encoding="utf-8") as f:
                json.dump(self.team_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[TeamCollaboration] Ошибка сохранения: {e}")

    def add_team_member(self, name: str, role: str, contact: str = ""):
        """Добавляет члена команды"""
        member = {
            "name": name,
            "role": role,
            "contact": contact,
            "added_at": datetime.now().isoformat()
        }

        # Проверяем дубликаты
        for m in self.team_data["team_members"]:
            if m["name"] == name:
                return False

        self.team_data["team_members"].append(member)
        self._save_team_data()
        return True

    def add_project(self, name: str, description: str = "", deadline: str = ""):
        """Добавляет проект"""
        project = {
            "name": name,
            "description": description,
            "deadline": deadline,
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "tasks": []
        }

        self.team_data["projects"].append(project)
        self._save_team_data()
        return True

    def add_task(self, project_name: str, task: str, priority: str = "medium", assignee: str = ""):
        """Добавляет задачу в проект"""
        for project in self.team_data["projects"]:
            if project["name"] == project_name:
                task_obj = {
                    "task": task,
                    "priority": priority,
                    "assignee": assignee,
                    "status": "pending",
                    "created_at": datetime.now().isoformat()
                }
                project["tasks"].append(task_obj)
                self._save_team_data()
                return True
        return False

    def analyze_communication(self, communication_log: List[Dict]) -> Dict:
        """
        Анализирует коммуникации для повышения эффективности

        Args:
            communication_log: Лог коммуникаций (кто, когда, тип)

        Returns:
            Dict с анализом и рекомендациями
        """
        analysis = {
            "total_communications": len(communication_log),
            "by_type": {},
            "by_member": {},
            "response_time_avg": 0,
            "recommendations": []
        }

        # Анализ по типам
        for comm in communication_log:
            comm_type = comm.get("type", "unknown")
            analysis["by_type"][comm_type] = analysis["by_type"].get(comm_type, 0) + 1

        # Анализ по участникам
        for comm in communication_log:
            member = comm.get("member", "unknown")
            analysis["by_member"][member] = analysis["by_member"].get(member, 0) + 1

        # Рекомендации
        if analysis["by_type"].get("meeting", 0) > 5:
            analysis["recommendations"].append("Много встреч. Рассмотрите асинхронную коммуникацию.")

        if analysis["by_type"].get("email", 0) > 10:
            analysis["recommendations"].append("Много email. Используйте мессенджеры для быстрых вопросов.")

        # Находим самых активных участников
        if analysis["by_member"]:
            most_active = max(analysis["by_member"], key=analysis["by_member"].get)
            analysis["recommendations"].append(f"{most_active} самый активный. Делегируйте часть задач.")

        return analysis

    def get_project_status(self, project_name: str) -> Optional[Dict]:
        """Получает статус проекта"""
        for project in self.team_data["projects"]:
            if project["name"] == project_name:
                total_tasks = len(project["tasks"])
                completed = sum(1 for t in project["tasks"] if t["status"] == "completed")
                pending = sum(1 for t in project["tasks"] if t["status"] == "pending")

                return {
                    "name": project["name"],
                    "status": project["status"],
                    "total_tasks": total_tasks,
                    "completed": completed,
                    "pending": pending,
                    "progress": (completed / total_tasks * 100) if total_tasks > 0 else 0,
                    "deadline": project["deadline"]
                }
        return None

    def suggest_team_actions(self, context: Dict) -> List[str]:
        """
        Предлагает действия для командной работы

        Args:
            context: Текущий контекст

        Returns:
            Список предложений
        """
        suggestions = []

        # Проверяем дедлайны проектов
        now = datetime.now()
        for project in self.team_data["projects"]:
            if project["deadline"]:
                try:
                    deadline = datetime.fromisoformat(project["deadline"])
                    days_left = (deadline - now).days
                    if days_left <= 3 and days_left > 0:
                        suggestions.append(f"Сэр, проект '{project['name']}' дедлайн через {days_left} дней. Нужно ускорить работу?")
                    elif days_left <= 0:
                        suggestions.append(f"Сэр, проект '{project['name']}' просрочен! Срочно!")
                except Exception as exc:
                    _logger.debug("Подавлено исключение: %s", exc, exc_info=True)

        # Проверяем незавершённые задачи
        for project in self.team_data["projects"]:
            pending_tasks = [t for t in project["tasks"] if t["status"] == "pending"]
            if len(pending_tasks) > 5:
                suggestions.append(f"Сэр, в проекте '{project['name']}' {len(pending_tasks)} незавершённых задач. Расставьте приоритеты.")

        # Проверяем активность команды
        if not self.team_data["team_members"]:
            suggestions.append("Сэр, хотите добавить членов команды для совместной работы?")

        return suggestions[:3]

    def generate_team_report(self) -> str:
        """Генерирует отчёт по командной работе"""
        report = "📊 Отчёт по командной работе\n\n"

        # Команда
        if self.team_data["team_members"]:
            report += "👥 Команда:\n"
            for member in self.team_data["team_members"]:
                report += f"  - {member['name']} ({member['role']})\n"
            report += "\n"

        # Проекты
        if self.team_data["projects"]:
            report += "📁 Проекты:\n"
            for project in self.team_data["projects"]:
                status = self.get_project_status(project["name"])
                if status:
                    report += f"  - {project['name']}: {status['completed']}/{status['total_tasks']} задач ({status['progress']:.0f}%)\n"
            report += "\n"

        # Задачи
        total_pending = 0
        for project in self.team_data["projects"]:
            total_pending += sum(1 for t in project["tasks"] if t["status"] == "pending")

        if total_pending > 0:
            report += f"⏳ Незавершённых задач: {total_pending}\n"

        return report


# Тестирование
if __name__ == "__main__":
    from pathlib import Path

    engine = TeamCollaborationEngine(Path(__file__).parent.parent)

    # Тест добавления данных
    engine.add_team_member("Александр", "Разработчик", "alex@example.com")
    engine.add_project("Веб-сайт", "Корпоративный сайт компании", "2025-12-31")
    engine.add_task("Веб-сайт", "Сделать главную страницу", "high", "Александр")

    print(engine.generate_team_report())

    # Тест предложений
    suggestions = engine.suggest_team_actions({"current_activity": "working"})
    print(f"\nПредложения: {suggestions}")
