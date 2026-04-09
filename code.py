from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import date, timedelta as td
import logging

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    kpi_role = fields.Selection([
        ('jr_dev',   'Jr. Developer'),
        ('sr_dev',   'Sr. Developer'),
        ('hr',       'HR Manager'),
        ('sales_pm', 'Sales/Project Manager'),
    ],
        string="KPI Role",
        compute='_compute_kpi_role',
        store=True,
        readonly=False,
    )

    monthly_salary = fields.Float(
    string='Monthly Salary',
    default=0.0,

    help='Used For KPI Bonus Calculation'
)

    @api.depends('job_id')
    def _compute_kpi_role(self):
        for emp in self:
            if emp.user_id and emp.user_id.id == 1:
               emp.kpi_role = False
               continue
            
            if emp.job_id:
                mapping = self.env['kpi.role.mapping'].search([
                    ('job_id', '=', emp.job_id.id)
                ], limit=1)
                emp.kpi_role = mapping.kpi_role if mapping else False
            else:
                emp.kpi_role = False

    current_weekly_kpi = fields.Float(
        string="Current Weekly KPI",
        readonly=True,
        default=0.0
    )

    last_month_kpi = fields.Float(
        string="Last Month KPI",
        readonly=True,
        default=0.0
    )

    source_config_line_ids = fields.One2many(
        'kpi.source.config.line',
        'employee_id',
        string="Source Configurations"
    )

    def get_slack_line(self):
        self.ensure_one()
        return self.source_config_line_ids.filtered(
            lambda l: l.config_id.source_type == 'slack' and l.active
        )[:1]

    def get_github_line(self):
        self.ensure_one()
        return self.source_config_line_ids.filtered(
            lambda l: l.config_id.source_type == 'github' and l.active
        )[:1]

    def get_slack_config(self):
        line = self.get_slack_line()
        return line.config_id if line else False

    def get_slack_member_id(self):
        line = self.get_slack_line()
        return line.member_id if line else ''

    def get_github_config(self):
        line = self.get_github_line()
        return line.config_id if line else False

    def get_github_username(self):
        line = self.get_github_line()
        return line.username if line else ''

    def action_test_slack_connection(self):
        self.ensure_one()

        slack_cfg = self.get_slack_config()
        member_id = self.get_slack_member_id()

        if not slack_cfg:
            raise UserError(
                "No Slack config found for %s.\n"
                "Please add a Slack source config." % self.name
            )

        activity_map = self.env['kpi.slack.service'].get_today_group_activity(
                source_config=slack_cfg
        )

        stats   = activity_map.get(member_id, {})

        message_count = stats.get('messages', 0)
        mention_count = stats.get('mentions', 0)
        channels = ", ".join(stats.get('channels', []))
        average_response_time = stats.get('average_response_time', 0.0)
        thread_replies = stats.get('threads', 0)

        self.message_post(body=(
            "Slack Data\n"
            "Total Messages: %d\n"
            "Mentions: %d\n"
            "Channels: %s\n"
            "Thread Replies: %d\n"
            "Average Response Time: %.2f mins"
        ) % (
              message_count,
              mention_count,
              channels,
              thread_replies,
              average_response_time))

    def action_calculate_kpi(self):
        self.ensure_one()

        if self.user_id and self.user_id.has_group('base.group_system'):
            raise UserError(
                "KPI calculation is not allowed for Admin/System users."
            )

        if not self.kpi_role:
            raise UserError(
                "KPI Role is not set for %s. "
                "Please set the KPI Role before calculating." % self.name
            )

        today      = date.today()
        start_date = today - td(days=7)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Calculate KPI',
            'res_model': 'kpi.calculate.wizard',
            'view_mode': 'form',
            'target':    'new',
            'context': {
                'default_employee_id': self.id,
                'default_week_start':  str(start_date),
                'default_number_of_days': 7,
            }
        }

    def update_current_kpi(self, start_date):
        self.ensure_one()

        kpi_record = self.env['kpi.score'].search([
            ('employee_id', '=', self.id),
            ('start_date',  '=', start_date),
        ], limit=1)

        _logger.info(
            "update_current_kpi called for %s | Date=%s | score=%s",
            self.name,
            start_date,
            kpi_record.score_total if kpi_record else 'NO RECORD FOUND'
        )
        if kpi_record:
            self.current_weekly_kpi = kpi_record.score_total


    def update_last_month_kpi(self):
        self.ensure_one()
        from datetime import date, timedelta

        today = date.today()
        first_day_this_month = today.replace(day=1)
        end_of_last_month = first_day_this_month - timedelta(days=1)
        start_of_last_month = end_of_last_month.replace(day=1)

        last_month_records = self.env['kpi.score'].search([
            ('employee_id', '=', self.id),
            ('end_date', '>=', start_of_last_month),
            ('end_date', '<=', end_of_last_month)
        ])

        if last_month_records:
            total_score = sum(last_month_records.mapped('score_total'))
            avg_score = total_score / len(last_month_records)
            self.last_month_kpi = round(avg_score, 2)
            _logger.info(
                "Last Month KPI updated for %s | Period: %s to %s | Avg Score: %s",
                self.name, start_of_last_month, end_of_last_month, self.last_month_kpi
            )
        else:
            self.last_month_kpi = 0.0
            _logger.info("No KPI records found for Last Month for %s", self.name)
