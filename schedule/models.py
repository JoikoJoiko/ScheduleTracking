from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Named(models.Model):
    name = models.CharField('Название', max_length=240)
    class Meta:
        abstract = True
        ordering = ['name']
    def __str__(self):
        return self.name


class AcademicYear(Named):
    start_date = models.DateField('Начало')
    end_date = models.DateField('Окончание')
    status = models.CharField('Статус', max_length=12, choices=[('draft', 'Черновик'), ('active', 'Действует'), ('archived', 'Архив')], default='draft')
    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError('Окончание раньше начала')
        if AcademicYear.objects.exclude(pk=self.pk).filter(start_date__lte=self.end_date, end_date__gte=self.start_date).exists():
            raise ValidationError('Учебные годы не должны пересекаться')
    class Meta(Named.Meta):
        verbose_name = 'Учебный год'
        verbose_name_plural = 'Учебные годы'


class Period(Named):
    year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, verbose_name='Учебный год')
    start_date = models.DateField('Начало')
    end_date = models.DateField('Окончание')
    def clean(self):
        if self.start_date > self.end_date or not (self.year.start_date <= self.start_date <= self.end_date <= self.year.end_date):
            raise ValidationError('Период должен находиться внутри учебного года')
    class Meta(Named.Meta):
        verbose_name = 'Учебный период'
        verbose_name_plural = 'Учебные периоды'


class Building(Named):
    address = models.CharField('Адрес', max_length=300, blank=True)
    description = models.TextField('Описание', blank=True)
    is_active = models.BooleanField('Действует', default=True)
    class Meta(Named.Meta):
        verbose_name = 'Корпус'
        verbose_name_plural = 'Корпуса'


class RoomType(Named):
    class Meta(Named.Meta):
        verbose_name = 'Тип помещения'
        verbose_name_plural = 'Типы помещений'


class Room(models.Model):
    number = models.CharField('Номер', max_length=60)
    building = models.ForeignKey(Building, on_delete=models.PROTECT, verbose_name='Корпус')
    floor = models.IntegerField('Этаж', null=True, blank=True)
    capacity = models.PositiveIntegerField('Вместимость', null=True, blank=True)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, verbose_name='Тип помещения')
    description = models.TextField('Описание и оснащение', blank=True)
    is_active = models.BooleanField('Доступна', default=True)
    def __str__(self):
        return f'{self.number} · {self.building}'
    class Meta:
        ordering = ['building__name', 'number']
        constraints = [models.UniqueConstraint(fields=['building', 'number'], name='room_building_number')]
        verbose_name = 'Аудитория'
        verbose_name_plural = 'Аудитории'


class Teacher(Named):
    is_active = models.BooleanField('Действует', default=True)
    class Meta(Named.Meta):
        verbose_name = 'Преподаватель'
        verbose_name_plural = 'Преподаватели'


class Specialty(Named):
    code = models.CharField('Код', max_length=30, unique=True)
    class Meta(Named.Meta):
        verbose_name = 'Специальность'
        verbose_name_plural = 'Специальности'


GROUP_STATUS = [('active', 'Активная'), ('graduated', 'Выпущена'), ('archived', 'Архивная'), ('disbanded', 'Расформирована')]
class StudentGroup(Named):
    name = models.CharField('Название', max_length=100, unique=True)
    specialty = models.ForeignKey(Specialty, on_delete=models.PROTECT, verbose_name='Специальность')
    building = models.ForeignKey(Building, on_delete=models.PROTECT, verbose_name='Корпус')
    admission_year = models.PositiveIntegerField('Год поступления')
    graduation_year = models.PositiveIntegerField('Год выпуска')
    status = models.CharField('Статус', max_length=15, choices=GROUP_STATUS, default='active')
    class Meta(Named.Meta):
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'


class GroupYear(models.Model):
    group = models.ForeignKey(StudentGroup, on_delete=models.PROTECT, verbose_name='Группа')
    year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, verbose_name='Учебный год')
    course = models.PositiveSmallIntegerField('Курс', default=1)
    status = models.CharField('Статус', max_length=15, choices=GROUP_STATUS, default='active')
    def __str__(self):
        return f'{self.group} · {self.year} · {self.course} курс'
    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'year'], name='group_year_unique')]
        verbose_name = 'Группа в учебном году'
        verbose_name_plural = 'Состав учебных годов'


class Subgroup(Named):
    group = models.ForeignKey(StudentGroup, on_delete=models.PROTECT, verbose_name='Группа')
    def __str__(self):
        return f'{self.group} / {self.name}'
    class Meta(Named.Meta):
        constraints = [models.UniqueConstraint(fields=['group', 'name'], name='subgroup_unique')]
        verbose_name = 'Подгруппа'
        verbose_name_plural = 'Подгруппы'


class Subject(Named):
    short_name = models.CharField('Сокращение', max_length=80, blank=True)
    class Meta(Named.Meta):
        verbose_name = 'Дисциплина'
        verbose_name_plural = 'Дисциплины'


class TimeSlot(Named):
    start_time = models.TimeField('Начало')
    end_time = models.TimeField('Окончание')
    sort_order = models.PositiveSmallIntegerField('Номер пары', unique=True)
    def clean(self):
        if self.start_time >= self.end_time:
            raise ValidationError('Окончание пары должно быть позже начала')
    class Meta:
        ordering = ['sort_order']
        verbose_name = 'Пара'
        verbose_name_plural = 'Звонки'


class ScheduleVersion(Named):
    period = models.ForeignKey(Period, on_delete=models.PROTECT, verbose_name='Период')
    effective_from = models.DateField('Действует с')
    effective_to = models.DateField('Действует по')
    status = models.CharField('Статус', max_length=15, choices=[('draft', 'Черновик'), ('published', 'Опубликовано'), ('archived', 'Архив')], default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    def clean(self):
        if not (self.period.start_date <= self.effective_from <= self.effective_to <= self.period.end_date):
            raise ValidationError('Срок действия должен находиться внутри периода')
    class Meta(Named.Meta):
        verbose_name = 'Версия расписания'
        verbose_name_plural = 'Версии расписания'


class Lesson(models.Model):
    version = models.ForeignKey(ScheduleVersion, on_delete=models.PROTECT, related_name='lessons', verbose_name='Версия')
    day_of_week = models.PositiveSmallIntegerField('День недели (0 — понедельник)', choices=list(enumerate(['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'])))
    week_type = models.CharField('Неделя', max_length=8, choices=[('all', 'Каждая'), ('odd', 'Нечётная'), ('even', 'Чётная')], default='all')
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, verbose_name='Пара')
    start_time = models.TimeField('Свое время начала', null=True, blank=True)
    end_time = models.TimeField('Свое время окончания', null=True, blank=True)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, verbose_name='Дисциплина')
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, verbose_name='Преподаватель')
    room = models.ForeignKey(Room, on_delete=models.PROTECT, verbose_name='Аудитория')
    lesson_type = models.CharField('Вид занятия', max_length=80, blank=True)
    def clean(self):
        if bool(self.start_time) != bool(self.end_time) or (self.start_time and self.start_time >= self.end_time):
            raise ValidationError('Укажите корректный интервал занятия')
        if self.version.status != 'draft':
            raise ValidationError('Опубликованные версии неизменяемы. Создайте новую версию или замену.')
    def __str__(self):
        return f'{self.get_day_of_week_display()}, {self.time_slot}: {self.subject}'
    class Meta:
        verbose_name = 'Регулярное занятие'
        verbose_name_plural = 'Регулярные занятия'


class Participant(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='participants', verbose_name='Занятие')
    group = models.ForeignKey(StudentGroup, on_delete=models.PROTECT, verbose_name='Группа')
    subgroup = models.ForeignKey(Subgroup, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Подгруппа')
    def clean(self):
        if self.subgroup_id and self.subgroup.group_id != self.group_id:
            raise ValidationError('Подгруппа принадлежит другой группе')
    class Meta:
        verbose_name = 'Участник занятия'
        verbose_name_plural = 'Участники занятий'


class CalendarDay(models.Model):
    year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, verbose_name='Учебный год')
    date = models.DateField('Дата', unique=True)
    is_holiday = models.BooleanField('Нерабочий день', default=False)
    weekday_override = models.PositiveSmallIntegerField('Расписание дня недели (0–6)', null=True, blank=True)
    week_type = models.CharField('Тип недели', max_length=8, choices=[('', 'По календарю'), ('odd', 'Нечётная'), ('even', 'Чётная')], blank=True)
    note = models.CharField('Примечание', max_length=240, blank=True)
    def clean(self):
        if self.weekday_override is not None and self.weekday_override > 6:
            raise ValidationError('День недели от 0 до 6')
        if not self.year.start_date <= self.date <= self.year.end_date:
            raise ValidationError('Дата вне учебного года')
    def __str__(self):
        return f'{self.date}: {self.note}'
    class Meta:
        verbose_name = 'Календарное исключение'
        verbose_name_plural = 'Учебный календарь'


class Change(models.Model):
    target_date = models.DateField('Исходная дата')
    original = models.ForeignKey(Lesson, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Исходное занятие')
    kind = models.CharField('Изменение', max_length=10, choices=[('replace', 'Замена'), ('cancel', 'Отмена'), ('move', 'Перенос'), ('add', 'Дополнительное занятие')])
    new_date = models.DateField('Новая дата', null=True, blank=True)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Новая пара')
    start_time = models.TimeField('Новое начало', null=True, blank=True)
    end_time = models.TimeField('Новое окончание', null=True, blank=True)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Новая дисциплина')
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Новый преподаватель')
    room = models.ForeignKey(Room, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Новая аудитория')
    group = models.ForeignKey(StudentGroup, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Новая группа')
    subgroup = models.ForeignKey(Subgroup, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Новая подгруппа')
    whole_group = models.BooleanField('Снять деление на подгруппы', default=False)
    reason = models.TextField('Причина', blank=True)
    status = models.CharField('Статус', max_length=12, choices=[('draft', 'Черновик'), ('published', 'Опубликовано'), ('withdrawn', 'Отменено')], default='draft')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Автор')
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    def clean(self):
        if self.kind != 'add' and not self.original_id:
            raise ValidationError('Выберите исходное занятие')
        if self.kind == 'add' and (self.original_id or not all([self.time_slot_id, self.subject_id, self.teacher_id, self.room_id, self.group_id])):
            raise ValidationError('Для дополнительного занятия нужны пара, дисциплина, преподаватель, аудитория и группа; исходное занятие не указывается')
        if self.kind == 'move' and not self.new_date:
            raise ValidationError('Укажите новую дату')
        if bool(self.start_time) != bool(self.end_time) or (self.start_time and self.start_time >= self.end_time):
            raise ValidationError('Укажите корректное начало и окончание')
        if self.subgroup_id and self.subgroup.group_id != (self.group_id or (self.original.participants.first().group_id if self.original_id and self.original.participants.exists() else None)):
            raise ValidationError('Подгруппа не соответствует группе')
    def __str__(self):
        return f'{self.target_date} · {self.get_kind_display()} · №{self.pk or "новая"}'
    class Meta:
        ordering = ['-target_date', '-pk']
        verbose_name = 'Замена'
        verbose_name_plural = 'Замены'


class RoomBlock(models.Model):
    room = models.ForeignKey(Room, on_delete=models.PROTECT, verbose_name='Аудитория')
    start_at = models.DateTimeField('Начало')
    end_at = models.DateTimeField('Окончание')
    reason = models.CharField('Причина', max_length=300)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Автор')
    def clean(self):
        if self.start_at >= self.end_at:
            raise ValidationError('Неверный интервал блокировки')
    def __str__(self):
        return f'{self.room}: {self.reason}'
    class Meta:
        verbose_name = 'Блокировка аудитории'
        verbose_name_plural = 'Блокировки аудиторий'


class StaffScope(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name='Диспетчер')
    buildings = models.ManyToManyField(Building, verbose_name='Доступные корпуса')
    def __str__(self):
        return str(self.user)
    class Meta:
        verbose_name = 'Область доступа диспетчера'
        verbose_name_plural = 'Области доступа диспетчеров'


class ImportProfile(Named):
    kind = models.CharField('Формат', max_length=16, choices=[('rows', 'Строки с заголовками'), ('blocks', 'Блоки групп')], default='rows')
    mapping = models.JSONField('Сопоставление столбцов / конфигурация блоков', default=dict, blank=True)
    class Meta(Named.Meta):
        verbose_name = 'Профиль импорта'
        verbose_name_plural = 'Профили импорта'


class ImportBatch(models.Model):
    filename = models.CharField(max_length=250)
    file_hash = models.CharField(max_length=64)
    profile = models.ForeignKey(ImportProfile, on_delete=models.PROTECT)
    version = models.ForeignKey(ScheduleVersion, on_delete=models.PROTECT, null=True, blank=True)
    period = models.ForeignKey(Period, on_delete=models.PROTECT)
    effective_from = models.DateField()
    effective_to = models.DateField()
    payload = models.JSONField(default=list)
    errors = models.JSONField(default=list)
    status = models.CharField(max_length=16, default='preview')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    def __str__(self):
        return f'{self.filename} · {self.status}'
    class Meta:
        verbose_name = 'Импорт'
        verbose_name_plural = 'Импорты'


class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=50)
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Запись журнала'
        verbose_name_plural = 'Журнал действий'


class SystemSetting(models.Model):
    key = models.CharField('Ключ', max_length=100, unique=True)
    value = models.TextField('Значение')
    description = models.CharField('Описание', max_length=300, blank=True)
    def __str__(self):
        return self.key
    class Meta:
        verbose_name = 'Настройка'
        verbose_name_plural = 'Настройки'
