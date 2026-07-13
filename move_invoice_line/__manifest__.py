# -*- coding: utf-8 -*-
#############################################################################
#
#    Madfox Solutions
#
#    Copyright (C) 2021-TODAY Madfox Solutions(<https://www.madfox.solutions>).
#    Author: Layla Bahloul
#
#    You can modify it under the terms of the GNU AFFERO
#    GENERAL PUBLIC LICENSE (AGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU AFFERO GENERAL PUBLIC LICENSE (AGPL v3) for more details.
#
#    You should have received a copy of the GNU AFFERO GENERAL PUBLIC LICENSE
#    (AGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################

{
    'name': 'Taj Customizations',
    'version': '19.0.1.0.0',
    'summary': 'customizations for accounting add new fields to help compute profit for track ,route, order',
    'category': 'Accounting',
    'author': 'Madfox',
    'maintainer': 'Madfox solutions',
    'company': 'Madfox solutions',
    'website': 'https://www.madfox.solutions',
    'depends': ['base', 'sale', 'fleet', 'account', 'purchase', 'account_asset'],
    'data': [
        'views/account_move_views.xml',
        'views/sale_order_views.xml',
        'views/invoice_views.xml',
        'views/inherit_partner.xml',
        'views/inherit_purchase_order_views.xml',
        'report/inherit_purchase_template.xml',
        'views/inherit_account_asset_views.xml'

    ],
    'qweb': [],
    'license': 'AGPL-3',
    'installable': True,
    'application': False,
    'auto_install': False,
}
