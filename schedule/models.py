from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.core.validators import MinValueValidator, MaxValueValidator


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
    week_anchor_date = models.DateField('Опорный понедельник', null=True, blank=True,
        help_text='Понедельник, для которого точно известен номер недели. Пусто — понедельник начала учебного года.')
    week_anchor_number = models.PositiveSmallIntegerField('Неделя опорного понедельника', default=1,
        choices=[(1, 'Первая'), (2, 'Вторая')])
    status = models.CharField('Статус', max_length=12, choices=[('draft', 'Черновик'), ('active', 'Действует'), ('archived', 'Архив')], default='draft')
    def clean(self):
        if self.week_anchor_date and self.week_anchor_date.weekday() != 0:
            raise ValidationError({'week_anchor_date': 'Укажите понедельник'})
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
    specialty = models.ForeignKey(Specialty, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Специальность')
    building = models.ForeignKey(Building, on_delete=models.PROTECT, verbose_name='Корпус')
    admission_year = models.PositiveIntegerField('Год поступления')
    graduation_year = models.PositiveIntegerField('Год выпуска')
    status = models.CharField('Статус', max_length=15, choices=GROUP_STATUS, default='active')
    class Meta(Named.Meta):
        ordering = ['-admission_year', 'name']
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'


class GroupYear(models.Model):
    group = models.ForeignKey(StudentGroup, on_delete=models.PROTECT, verbose_name='Группа')
    year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, verbose_name='Учебный год')
    course = models.PositiveSmallIntegerField('Курс', default=1, validators=[MinValueValidator(1), MaxValueValidator(4)])
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
        ordering = ['-group__admission_year', 'group__name', 'name']
        constraints = [models.UniqueConstraint(fields=['group', 'name'], name='subgroup_unique')]
        verbose_name = 'Подгруппа'
        verbose_name_plural = 'Подгруппы'


class Subject(Named):
    short_name = models.CharField('Сокращение', max_length=80, blank=True)
    is_curricular = models.BooleanField('Учебная дисциплина', default=True)
    def save(self,*args,**kwargs):
        from .class_hours import is_class_hour
        if is_class_hour(self.name):
            self.is_curricular=False
            if kwargs.get('update_fields'):
                kwargs['update_fields']=set(kwargs['update_fields'])|{'is_curricular'}
        return super().save(*args,**kwargs)
    class Meta(Named.Meta):
        verbose_name = 'Дисциплина'
        verbose_name_plural = 'Дисциплины'


class TimeSlot(Named):
    start_time = models.TimeField('Начало', null=True, blank=True)
    end_time = models.TimeField('Окончание', null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField('Номер пары', unique=True)
    def clean(self):
        if bool(self.start_time) != bool(self.end_time) or (self.start_time and self.start_time >= self.end_time):
            raise ValidationError('Окончание пары должно быть позже начала')
        if self.sort_order and not self.start_time:
            raise ValidationError('Для пары укажите начало и окончание')
    class Meta:
        ordering = ['sort_order']
        verbose_name = 'Пара'
        verbose_name_plural = 'Звонки'


WEEKDAYS = list(enumerate(['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье']))


class WeekdayBell(models.Model):
    day_of_week = models.PositiveSmallIntegerField('День недели', choices=WEEKDAYS)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, verbose_name='Пара')
    start_time = models.TimeField('Начало')
    end_time = models.TimeField('Окончание')
    def clean(self):
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError('Окончание пары должно быть позже начала')
    def __str__(self):
        return f'{self.get_day_of_week_display()} · {self.time_slot}'
    class Meta:
        ordering = ['day_of_week', 'time_slot__sort_order']
        constraints = [models.UniqueConstraint(fields=['day_of_week', 'time_slot'], name='weekday_bell_unique')]
        verbose_name = 'Звонки по дню недели'
        verbose_name_plural = 'Звонки по дням недели'


class CuratorBell(models.Model):
    shift = models.PositiveSmallIntegerField('Смена', unique=True, choices=[(1,'Первая'),(2,'Вторая')])
    start_time = models.TimeField('Начало')
    end_time = models.TimeField('Окончание')
    def clean(self):
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError('Окончание кураторского часа должно быть позже начала')
    def __str__(self):
        return f'Кураторский час · {self.get_shift_display()} смена'
    class Meta:
        ordering = ['shift']
        verbose_name = 'Кураторский час в понедельник'
        verbose_name_plural = 'Кураторские часы в понедельник'


class ShortenedDay(models.Model):
    date = models.DateField('Дата', unique=True)
    lesson_count = models.PositiveSmallIntegerField('Количество пар',null=True,blank=True,validators=[MinValueValidator(1),MaxValueValidator(7)])
    lesson_minutes = models.PositiveSmallIntegerField('Длительность пары, минут', validators=[MinValueValidator(1), MaxValueValidator(180)])
    start_time = models.TimeField('Начало первой пары', null=True, blank=True)
    keep_breaks = models.BooleanField('Сохранить обычные перемены', default=True)
    break_minutes = models.PositiveSmallIntegerField('Перемена, минут', null=True, blank=True, validators=[MaxValueValidator(120)])
    is_active = models.BooleanField('Действует', default=True)
    note = models.CharField('Примечание', max_length=240, blank=True)
    def clean(self):
        if not self.keep_breaks and self.break_minutes is None:
            raise ValidationError({'break_minutes':'Укажите длительность перемены'})
        if self.date and self.date.weekday()==6:
            raise ValidationError({'date':'Выберите учебный день с понедельника по субботу'})
        if self.date and self.lesson_minutes:
            from .timing import shortened_times, regular_bell_times
            shortened_times(self.date,regular_bell_times(self.date),self,getattr(self,'_pending_bells',None))
    def __str__(self):
        return f'{self.date:%d.%m.%Y} · {self.lesson_minutes} мин'
    class Meta:
        ordering=['date']
        verbose_name='Сокращённый день'
        verbose_name_plural='Сокращённые дни'


class ShortenedBell(models.Model):
    shortened_day = models.ForeignKey(ShortenedDay,on_delete=models.CASCADE,related_name='bells',verbose_name='Сокращённый день')
    time_slot = models.ForeignKey(TimeSlot,on_delete=models.PROTECT,verbose_name='Пара')
    lesson_minutes = models.PositiveSmallIntegerField('Длительность пары, минут',null=True,blank=True,validators=[MinValueValidator(1),MaxValueValidator(180)])
    break_minutes = models.PositiveSmallIntegerField('Перемена после, минут',null=True,blank=True,validators=[MaxValueValidator(720)])
    def clean(self):
        if self.time_slot_id and self.time_slot.sort_order not in range(1,8):
            raise ValidationError('Настройки доступны для пар с первой по седьмую')
    def __str__(self):
        return f'{self.shortened_day} · {self.time_slot}'
    class Meta:
        ordering=['time_slot__sort_order']
        constraints=[models.UniqueConstraint(fields=['shortened_day','time_slot'],name='shortened_bell_unique')]
        verbose_name='Пара сокращённого дня'
        verbose_name_plural='Пары сокращённого дня'


class PracticePeriod(models.Model):
    group = models.ForeignKey(StudentGroup,on_delete=models.PROTECT,verbose_name='Группа')
    start_date = models.DateField('С')
    end_date = models.DateField('По (включительно)')
    title = models.CharField('Название',max_length=240,default='Практика')
    organization = models.CharField('Место практики',max_length=240,blank=True)
    is_active = models.BooleanField('Действует',default=True)
    def clean(self):
        if self.start_date and self.end_date:
            if self.start_date>self.end_date:
                raise ValidationError('Окончание практики раньше начала')
            if self.is_active and self.group_id and PracticePeriod.objects.exclude(pk=self.pk).filter(
                group_id=self.group_id,is_active=True,start_date__lte=self.end_date,end_date__gte=self.start_date).exists():
                raise ValidationError('У группы уже есть практика в этом диапазоне дат')
    def __str__(self):
        return f'{self.group} · {self.start_date:%d.%m}–{self.end_date:%d.%m.%Y}'
    class Meta:
        ordering=['-start_date','group__name']
        verbose_name='Практика группы'
        verbose_name_plural='Практики групп'


class ScheduleVersion(Named):
    period = models.ForeignKey(Period, on_delete=models.PROTECT, verbose_name='Период')
    effective_from = models.DateField('Действует с')
    effective_to = models.DateField('Действует по')
    status = models.CharField('Статус', max_length=15, choices=[('draft', 'Черновик'), ('published', 'Опубликовано'), ('archived', 'Архив')], default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    groups = models.ManyToManyField(StudentGroup, blank=True, verbose_name='Группы этой версии')
    def clean(self):
        if not (self.period.start_date <= self.effective_from <= self.effective_to <= self.period.end_date):
            raise ValidationError('Срок действия должен находиться внутри периода')
    class Meta(Named.Meta):
        verbose_name = 'Версия расписания'
        verbose_name_plural = 'Версии расписания'


class Lesson(models.Model):
    version = models.ForeignKey(ScheduleVersion, on_delete=models.PROTECT, related_name='lessons', verbose_name='Версия')
    day_of_week = models.PositiveSmallIntegerField('День недели (0 — понедельник)', choices=list(enumerate(['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'])))
    week_type = models.CharField('Неделя', max_length=8, choices=[('all', 'Каждая'), ('odd', 'Первая'), ('even', 'Вторая')], default='all')
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.PROTECT, verbose_name='Пара')
    start_time = models.TimeField('Свое время начала', null=True, blank=True)
    end_time = models.TimeField('Свое время окончания', null=True, blank=True)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, verbose_name='Дисциплина')
    teacher = models.ForeignKey(Teacher, on_delete=models.PROTECT, verbose_name='Преподаватель')
    room = models.ForeignKey(Room, on_delete=models.PROTECT, verbose_name='Аудитория')
    lesson_type = models.CharField('Вид занятия', max_length=80, blank=True)
    curator_shift = models.PositiveSmallIntegerField('Смена кураторского часа', null=True, blank=True,
        choices=[(1,'Первая'),(2,'Вторая')])
    def clean(self):
        if bool(self.start_time) != bool(self.end_time) or (self.start_time and self.start_time >= self.end_time):
            raise ValidationError('Укажите корректный интервал занятия')
        if self.curator_shift and self.time_slot_id and self.time_slot.sort_order!=0:
            raise ValidationError({'curator_shift':'Смена указывается только для кураторского часа без номера пары'})
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
    week_type = models.CharField('Неделя в этот день', max_length=8, choices=[('', 'По календарю'), ('odd', 'Первая'), ('even', 'Вторая')], blank=True)
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
    target_group = models.ForeignKey(StudentGroup, on_delete=models.PROTECT, null=True, blank=True, related_name='targeted_changes', verbose_name='Только для группы')
    target_subgroup = models.ForeignKey(Subgroup,on_delete=models.PROTECT,null=True,blank=True,related_name='targeted_changes',verbose_name='Только для подгруппы')
    subgroup = models.ForeignKey(Subgroup, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Новая подгруппа')
    whole_group = models.BooleanField('Снять деление на подгруппы', default=False)
    reason = models.TextField('Причина', blank=True)
    room_overlap_approvals = models.JSONField('Разрешённые пересечения кабинета',default=list,blank=True,editable=False)
    status = models.CharField('Статус', max_length=12, choices=[('draft', 'Черновик'), ('published', 'Опубликовано'), ('withdrawn', 'Отменено')], default='draft')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Автор')
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    def clean(self):
        if self.kind != 'add' and not self.original_id:
            raise ValidationError('Выберите исходное занятие')
        if self.target_group_id and (not self.original_id or not self.original.participants.filter(group_id=self.target_group_id).exists()):
            raise ValidationError('Выбранная группа не участвует в исходном занятии')
        if self.target_subgroup_id:
            if not self.target_group_id or self.target_subgroup.group_id!=self.target_group_id:
                raise ValidationError('Укажите группу выбранной исходной подгруппы')
            participants=self.original.participants.filter(group_id=self.target_group_id) if self.original_id else Participant.objects.none()
            if not participants.filter(Q(subgroup_id=self.target_subgroup_id)|Q(subgroup__isnull=True)).exists():
                raise ValidationError('Подгруппа не участвует в исходном занятии')
            if participants.filter(subgroup__isnull=True).exists() and not Subgroup.objects.filter(group_id=self.target_group_id).exclude(pk=self.target_subgroup_id).exists():
                raise ValidationError('Сначала добавьте остальные подгруппы группы в справочник')
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
    kind = models.CharField('Формат', max_length=16, choices=[('college', 'Расписание колледжа'), ('rows', 'Строки с заголовками'), ('blocks', 'Блоки групп')], default='rows')
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
    metadata = models.JSONField(default=dict, blank=True)
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


class ImageImport(models.Model):
    image = models.ImageField('Изображение', upload_to='changes/%Y/%m/')
    target_date = models.DateField('Дата замен')
    layout = models.CharField('Формат', max_length=12, choices=[('table','Таблица замен'),('yellow','Жёлтые ячейки')])
    payload = models.JSONField('Распознанные строки', default=list, blank=True)
    warnings = models.JSONField('Предупреждения', default=list, blank=True)
    status = models.CharField('Статус', max_length=12, default='review', choices=[('review','Проверка'),('published','Опубликовано'),('cancelled','Отменено')])
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='Автор')
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = 'Загрузка замен'
        verbose_name_plural = 'Замены из изображений'
    def __str__(self):
        return f'{self.target_date} · {self.get_layout_display()}'
