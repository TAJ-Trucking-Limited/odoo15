# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_is_zero

VAT_PERCENTAGE_DIGITS = 4


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    vat_percentage = fields.Float(
        string='VAT (%)',
        digits=(16, VAT_PERCENTAGE_DIGITS),
        help="VAT rate applied to every product line of this order. "
             "It replaces the taxes coming from the products.",
    )
    vat_percentage_active = fields.Boolean(
        string='VAT Percentage Applied',
        default=False,
        copy=True,
        help="Technical marker set when VAT (%) is explicitly set on this "
             "order. Existing orders stay inactive after the module upgrade "
             "so their lines are left untouched.",
    )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @api.model
    def _check_vat_percentage_range(self, rate):
        rate = float(rate or 0.0)
        if (
            float_compare(rate, 0.0, precision_digits=VAT_PERCENTAGE_DIGITS) < 0
            or float_compare(
                rate, 100.0, precision_digits=VAT_PERCENTAGE_DIGITS,
            ) > 0
        ):
            raise ValidationError(_(
                "The VAT (%%) rate must be between 0 and 100 (inclusive). "
                "Received: %(rate)s.",
                rate='%g' % rate,
            ))
        return rate

    @api.constrains('vat_percentage')
    def _check_vat_percentage(self):
        for order in self:
            order._check_vat_percentage_range(order.vat_percentage)

    def _check_vat_percentage_change_allowed(self, new_rate):
        """Block rate changes once invoices were generated from the order."""
        self.ensure_one()
        if not self.invoice_ids:
            return
        same_rate = (
            self.vat_percentage_active
            and float_compare(
                new_rate,
                self.vat_percentage,
                precision_digits=VAT_PERCENTAGE_DIGITS,
            ) == 0
        )
        if not same_rate:
            raise UserError(_(
                "The VAT (%%) of sales order %(order)s cannot be changed "
                "because invoices were already generated from it.",
                order=self.display_name,
            ))

    # ------------------------------------------------------------------
    # Tax resolution
    # ------------------------------------------------------------------

    def _get_vat_tax_country(self):
        """Return the country the VAT tax must belong to."""
        self.ensure_one()
        if 'tax_country_id' in self._fields:
            return self.tax_country_id
        if self.fiscal_position_id.foreign_vat:
            return self.fiscal_position_id.country_id
        return self.company_id.account_fiscal_country_id

    def _get_vat_tax_country_for_vals(self, vals):
        self.ensure_one()
        if 'fiscal_position_id' in vals:
            fiscal_position = self.env['account.fiscal.position'].browse(
                vals['fiscal_position_id'],
            )
            if fiscal_position.foreign_vat:
                return fiscal_position.country_id
            return self.company_id.account_fiscal_country_id
        return self._get_vat_tax_country()

    def _get_vat_percentage_tax(self, rate=None, country=None):
        """Return the single eligible sale tax for the order VAT rate.

        A rate of 0% returns an empty recordset: the feature clears taxes
        instead of assigning a 0% tax record.
        """
        self.ensure_one()
        rate = self._check_vat_percentage_range(
            self.vat_percentage if rate is None else rate,
        )
        if float_is_zero(rate, precision_digits=VAT_PERCENTAGE_DIGITS):
            return self.env['account.tax']

        precision = 10 ** -VAT_PERCENTAGE_DIGITS
        domain = [
            ('company_id', '=', self.company_id.id),
            ('active', '=', True),
            ('type_tax_use', '=', 'sale'),
            ('amount_type', '=', 'percent'),
            ('amount', '>=', rate - precision),
            ('amount', '<=', rate + precision),
        ]
        if country is None:
            country = self._get_vat_tax_country()
        if country:
            domain.append(('country_id', '=', country.id))
        taxes = self.env['account.tax'].search(domain).filtered(
            lambda tax: (
                not tax.price_include
                and float_compare(
                    tax.amount,
                    rate,
                    precision_digits=VAT_PERCENTAGE_DIGITS,
                ) == 0
            ),
        )
        if len(taxes) != 1:
            raise ValidationError(self._get_vat_tax_error_message(
                rate, country, taxes,
            ))
        return taxes

    def _get_vat_tax_error_message(self, rate, country, taxes):
        self.ensure_one()
        country_label = country.display_name if country else _("not defined")
        if not taxes:
            return _(
                "No sale tax matches the VAT (%%) rate %(rate)s%% for company "
                "%(company)s (tax country: %(country)s). Configure exactly "
                "one active, price-excluded percentage sale tax with this "
                "rate, then set the VAT (%%) again.",
                rate='%g' % rate,
                company=self.company_id.display_name,
                country=country_label,
            )
        return _(
            "Several sale taxes match the VAT (%%) rate %(rate)s%% for "
            "company %(company)s (tax country: %(country)s): %(taxes)s. Keep "
            "exactly one eligible tax, then set the VAT (%%) again.",
            rate='%g' % rate,
            company=self.company_id.display_name,
            country=country_label,
            taxes=', '.join(taxes.mapped('display_name')),
        )

    # ------------------------------------------------------------------
    # Line synchronization
    # ------------------------------------------------------------------

    def _apply_vat_percentage_to_lines(self):
        """Force the resolved VAT tax on every non-display order line."""
        for order in self:
            if not order.vat_percentage_active:
                continue
            tax = order._get_vat_percentage_tax()
            lines = order.order_line.filtered(
                lambda line: not line.display_type,
            )
            lines._set_vat_percentage_tax(tax)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'vat_percentage' in vals:
                vals['vat_percentage_active'] = True
                self._check_vat_percentage_range(vals['vat_percentage'])
        orders = super().create(vals_list)
        orders._apply_vat_percentage_to_lines()
        return orders

    def write(self, vals):
        if 'vat_percentage' in vals:
            rate = self._check_vat_percentage_range(vals['vat_percentage'])
            for order in self:
                order._check_vat_percentage_change_allowed(rate)
                order._get_vat_percentage_tax(
                    rate=rate,
                    country=order._get_vat_tax_country_for_vals(vals),
                )
            vals = dict(vals, vat_percentage_active=True)
        result = super().write(vals)
        if 'vat_percentage' in vals:
            self._apply_vat_percentage_to_lines()
        return result

    # ------------------------------------------------------------------
    # Invoicing
    # ------------------------------------------------------------------

    def _prepare_invoice(self):
        values = super()._prepare_invoice()
        values.update({
            'vat_percentage': self.vat_percentage,
            'is_vat_percentage_invoice': bool(self.vat_percentage_active),
        })
        return values

    def _get_invoice_grouping_keys(self):
        return super()._get_invoice_grouping_keys() + [
            'vat_percentage',
            'is_vat_percentage_invoice',
        ]
