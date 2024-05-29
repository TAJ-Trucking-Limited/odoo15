import datetime
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class ReportTajProfitExcel(models.AbstractModel):
    _name = 'report.send_report_via_mail.profit_loss_report'
    _inherit = 'report.report_xlsx.abstract'

    def generate_xlsx_report(self, workbook, data, partners):
        # print('data:::', data['products'])
        bold = workbook.add_format({'bold': True})
        sheet = workbook.add_worksheet("sheet")
        sheet.set_column(0, 80, 30)
        sheet.write(0, 0, 'Truck', bold)
        sheet.write(0, 1, 'Order', bold)
        sheet.write(0, 2, 'Rout', bold)
        sheet.write(0, 3, 'Trip', bold)
        sheet.write(0, 4, 'Weight', bold)
        sheet.write(0, 5, 'Year', bold)
        sheet.write(0, 6, 'Operating Income', bold)
        sheet.write(0, 7, 'Transit fees Documentation Fees', bold)
        sheet.write(0, 8, 'Transit fees Levy Council Fee Nakonde', bold)
        sheet.write(0, 9, 'Transit fees Levy Council Fee Kapiri', bold)
        sheet.write(0, 10, 'Transit fees Parking Security Fees going', bold)
        sheet.write(0, 11, 'Transit fees Road Permit', bold)
        sheet.write(0, 12, 'Transit fees Electronic Seal', bold)
        sheet.write(0, 13, 'Transit fees Border Fees', bold)
        sheet.write(0, 14, 'Transit fees First Entry', bold)
        sheet.write(0, 15, 'Transit fees Lashing Fees', bold)
        sheet.write(0, 16, 'Transit fees Abnormalm Signage', bold)
        sheet.write(0, 17, 'Transit fees GCLA Loading Facilitation Permit', bold)
        sheet.write(0, 18, 'Transit fees Weighbridge Fees', bold)
        sheet.write(0, 19, 'Transit fees Peage', bold)
        sheet.write(0, 20, 'Transit fees Levy Council Fee Tunduma', bold)
        sheet.write(0, 21, 'Transit fees Cargo Rearrangement', bold)
        sheet.write(0, 22, 'Transit fees Demurrage Fee', bold)
        sheet.write(0, 23, 'Driver Trip Allowance Expense Transit', bold)
        sheet.write(0, 24, 'Transit fees Bond going', bold)
        sheet.write(0, 25, 'Transit fees TollRoad', bold)
        sheet.write(0, 26, 'Transit fees GCLA Loading Facilitation Other going', bold)
        sheet.write(0, 27, 'Toll Gates going', bold)
        sheet.write(0, 28, 'Return fees Container TAX_going', bold)
        sheet.write(0, 29, 'Late Exit Note going', bold)
        sheet.write(0, 30, 'Carbon Tax going', bold)
        sheet.write(0, 31, 'Return fees weight pridje going', bold)
        sheet.write(0, 32, 'wating charges going', bold)
        sheet.write(0, 33, 'Total Going', bold)
        sheet.write(0, 34, 'Mbeya', bold)
        sheet.write(0, 35, 'kibaha', bold)
        sheet.write(0, 36, 'Morogoro', bold)
        sheet.write(0, 37, 'Tunduma', bold)
        sheet.write(0, 38, 'Total Fuel', bold)
        sheet.write(0, 39, 'Return fees Carrier License', bold)
        sheet.write(0, 40, 'Return fee Over Stay', bold)
        sheet.write(0, 41, 'Return fees Cargo Rearrangement', bold)
        sheet.write(0, 42, 'Return fees Radiation Protection Fee', bold)
        sheet.write(0, 43, 'Return fees Weight Check Ndola', bold)
        sheet.write(0, 44, 'Return fees Parking Security Fees', bold)
        sheet.write(0, 45, 'Return fee Over Stay', bold)
        sheet.write(0, 46, 'Return fees Entry Card', bold)
        sheet.write(0, 47, 'Return fees Kanyaka', bold)
        sheet.write(0, 48, 'Return fees Levy Council Fee Kapiri', bold)
        sheet.write(0, 49, 'Return fees Penalty over wight', bold)
        sheet.write(0, 50, 'Transit fees Bond', bold)
        sheet.write(0, 51, 'Transit fees Road Toll', bold)
        sheet.write(0, 52, 'Transit fees GCLA Loading Facilitation Other', bold)
        sheet.write(0, 53, 'Toll Gates', bold)
        sheet.write(0, 54, 'Late Exit Note', bold)
        sheet.write(0, 55, 'Return fees Container TAX', bold)
        sheet.write(0, 56, 'Carbon Tax', bold)
        sheet.write(0, 57, 'Return fees weight pridje', bold)
        sheet.write(0, 58, 'wating charges', bold)
        sheet.write(0, 59, 'Return fees Empty Container Offloading Fees', bold)
        sheet.write(0, 60, 'Return fees Visa', bold)
        sheet.write(0, 61, 'Return fees Weight Check Tunduma', bold)
        sheet.write(0, 62, 'Return fees Chemical transportation', bold)
        sheet.write(0, 63, 'Driver Trip Allowance Expense Return', bold)
        sheet.write(0, 64, 'Return fees Parking Security Fees', bold)
        sheet.write(0, 65, 'Total Return', bold)
        sheet.write(0, 66, 'Expenses', bold)
        sheet.write(0, 67, 'Cross Profit', bold)
        sheet.write(0, 68, '%', bold)

        # sheet.write(0, 13, 'Details', bold)

        # sheet.write(0, 5, 'Jt I sh', bold)
        # sheet.write(0, 6, 'Jt I Ml', bold)
        # sheet.write(0, 7, 'Jt I Ml Liq', bold)
        # sheet.write(0, 8, 'Last Purchase Date', bold)
        # sheet.write(0, 9, 'Out Of Stock Date', bold)
        # sheet.write(0, 10, 'Days Of Published', bold)
        # sheet.write(0, 11, 'Sales Price', bold)
        # sheet.write(0, 12, 'Quantity', bold)
        row = 0
        for obj in data['products']:
            row += 1
            sheet.write(row, 0, obj['license_plate'])
            sheet.write(row, 1, obj['order_name'])
            sheet.write(row, 2, obj['root'])
            sheet.write(row, 3, obj['trip'])
            sheet.write(row, 4, obj['size'])
            sheet.write(row, 5, obj['date'])
            sheet.write(row, 6, obj['operating_income'])
            sheet.write(row, 7, obj['Transit_fees_Documentation_Fees'], bold)
            sheet.write(row, 8, obj['Transit_fees_Levy_Council_Fee_Nakonde'], bold)
            sheet.write(row, 9, obj['Transit_fees_Levy_Council_Fee_Kapiri'], bold)
            sheet.write(row, 10, obj['Transit_fees_Parking_Security_Fees_going'], bold)
            sheet.write(row, 11, obj['Transit_fees_Road_Permit'], bold)
            sheet.write(row, 12, obj['Transit_fees_Electronic_Seal'], bold)
            sheet.write(row, 13, obj['Transit_fees_Border_Fees'], bold)
            sheet.write(row, 14, obj['Transit_fees_First_Entry'], bold)
            sheet.write(row, 15, obj['Transit_fees_Lashing_Fees'], bold)
            sheet.write(row, 16, obj['Transit_fees_Abnormalm_Signage'], bold)
            sheet.write(row, 17, obj['Transit_fees_GCLA_Loading_Facilitation_Permit'], bold)
            sheet.write(row, 18, obj['Transit_fees_Weighbridge_Fees'], bold)
            sheet.write(row, 19, obj['Transit_fees_Peage'], bold)
            sheet.write(row, 20, obj['Transit_fees_Levy_Council_Fee_Tunduma'], bold)
            sheet.write(row, 21, obj['Transit_fees_Cargo_Rearrangement'], bold)
            sheet.write(row, 22, obj['Transit_fees_Demurrage_Fee'], bold)
            sheet.write(row, 23, obj['Driver_Trip_Allowance_Expense_Transit'], bold)
            sheet.write(row, 24, obj['Transit_fees_Bond_going'], bold)
            sheet.write(row, 25, obj['Transit_fees_TollRoad'], bold)
            sheet.write(row, 26, obj['Transit_fees_GCLA_Loading_Facilitation_Other_going'], bold)
            sheet.write(row, 27, obj['Toll_Gates_going'], bold)
            sheet.write(row, 28, obj['Return_fees_Container_TAX_going'], bold)
            sheet.write(row, 29, obj['Late_Exit_Note_going'], bold)
            sheet.write(row, 30, obj['Carbon_Tax_going'], bold)
            sheet.write(row, 31, obj['Return_fees_weight_pridje_going'], bold)
            sheet.write(row, 32, obj['wating_charges_going'], bold)
            sheet.write(row, 33, obj['total_going'])
            sheet.write(row, 34, obj['mbeya'])
            sheet.write(row, 35, obj['kibaha'])
            sheet.write(row, 36, obj['morogoro'])
            sheet.write(row, 37, obj['tunduma'])
            sheet.write(row, 38, obj['total_fuel'])
            sheet.write(row, 39, obj['Return_fees_Carrier_License'])
            sheet.write(row, 40, obj['Return_fee_Over_Stay'])
            sheet.write(row, 41, obj['Return_fees_Cargo_Rearrangement'])
            sheet.write(row, 42, obj['Return_fees_Radiation_Protection_Fee'])
            sheet.write(row, 43, obj['Return_fees_Weight_Check_Ndola'])
            sheet.write(row, 44, obj['Return_fees_Parking_Security_Fees'])
            sheet.write(row, 45, obj['Return_fee_Over_Stay'])
            sheet.write(row, 46, obj['Return_fees_Entry_Card'])
            sheet.write(row, 47, obj['Return_fees_Kanyaka'])
            sheet.write(row, 48, obj['Return_fees_Levy_Council_Fee_Kapiri'])
            sheet.write(row, 49, obj['Return_fees_Penalty_over_wight'])
            sheet.write(row, 50, obj['Transit_fees_Bond'])
            sheet.write(row, 51, obj['Transit_fees_Road_Toll'])
            sheet.write(row, 52, obj['Transit_fees_GCLA_Loading_Facilitation_Other'])
            sheet.write(row, 53, obj['Toll_Gates'])
            sheet.write(row, 54, obj['Late_Exit_Note'])
            sheet.write(row, 55, obj['Return_fees_Container_TAX'])
            sheet.write(row, 56, obj['Carbon_Tax'])
            sheet.write(row, 57, obj['Return_fees_weight_pridje'])
            sheet.write(row, 58, obj['wating_charges'])
            sheet.write(row, 59, obj['Return_fees_Empty_Container_Offloading_Fees'])
            sheet.write(row, 60, obj['Return_fees_Visa'])
            sheet.write(row, 61, obj['Return_fees_Weight_Check_Tunduma'])
            sheet.write(row, 62, obj['Return_fees_Chemical_transportation'])
            sheet.write(row, 63, obj['Driver_Trip_Allowance_Expense_Return'])
            sheet.write(row, 64, obj['Return_fees_Parking_Security_Fees_ret'])
            sheet.write(row, 65, obj['total_return'])
            sheet.write(row, 66, obj['expenses'])
            sheet.write(row, 67, obj['cross_profit'])
            sheet.write(row, 68,
                        (obj['cross_profit'] / obj['operating_income'] * 100) if obj['operating_income'] != 0 else 0)

            # sheet.write(row, 13, obj['from_sum'])
