# -*- coding: utf-8 -*-
from src.services.prompt_registry import build_prompt


def test_required_products_prompt_keeps_direct_product_requests_narrow():
    prompt = build_prompt("required_products_for_task", task_description="Нужен перфоратор 800Вт")

    assert "не превращай запрос в большой проектный список" in prompt.system
    assert "Не добавляй опциональные аксессуары" in prompt.system
    assert "Не добавляй длинный список из сверл, долот, патронов, переходников" in prompt.system
    assert "не добавляй в text и products опциональную оснастку вроде **долота**" in prompt.system


def test_required_products_prompt_excludes_task_object_from_mounting_jobs():
    prompt = build_prompt("required_products_for_task", task_description="Хочу повесить полку на стену")

    assert "Объект задачи сам по себе не нужно включать в список товаров" in prompt.system
    assert "не надо добавлять 'полка' в products" in prompt.system
    assert "Не предлагай товары с явно несовместимым" in prompt.system
    assert "Не используй расплывчатые или неуверенные формулировки" in prompt.system
    assert "Не придумывай побочные предметы с сомнительной пользой вроде **шпателя**" in prompt.system


def test_consultation_prompt_is_short_and_direct():
    prompt = build_prompt("consultation_answer", question="Что лучше для стен: виниловые или флизелиновые обои?")

    assert "Идеальный ответ - 2-4 коротких предложения" in prompt.system
    assert "делай вывод сразу в первой фразе" in prompt.system
    assert "2-4 предложения максимум" in prompt.user


def test_relevance_prompt_allows_compatible_subtypes():
    prompt = build_prompt(
        "check_products_relevance",
        category_name="дюбели для бетона",
        products_list="Товар 1: Дюбель распорный 6x40мм",
    )

    assert "очевидно совместимым и корректным подтипом" in prompt.system
    assert "Для категории 'дюбели для бетона' подходят 'дюбель распорный'" in prompt.system
