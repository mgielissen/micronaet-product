# -*- coding: utf-8 -*-
###############################################################################
#
#    Copyright (C) 2001-2014 Micronaet SRL (<http://www.micronaet.it>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
import os
import sys
import logging
import openerp
import openerp.netsvc as netsvc
import openerp.addons.decimal_precision as dp
from openerp.osv import fields, osv, expression, orm
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from openerp import SUPERUSER_ID, api
from openerp import tools
from openerp.tools.translate import _
from openerp.tools.float_utils import float_round as round
from openerp.tools import (DEFAULT_SERVER_DATE_FORMAT, 
    DEFAULT_SERVER_DATETIME_FORMAT, 
    DATETIME_FORMATS_MAP, 
    float_compare)


_logger = logging.getLogger(__name__)

class ProductCostTransport(orm.Model):
    """ Product Cost Transport method
    """    
    _name = 'product.cost.transport'
    _description = 'Transport method'
    
    _columns = {
        'name': fields.char('Name', size=64, required=True),
        'cost': fields.float('Transport cost', 
            digits_compute=dp.get_precision('Product Price'), 
            help='Transport cost in company currency', required=False),
        'cube_meter': fields.float('M3 total', digits=(16, 2), required=False), 
        'note': fields.text('Note'),
        }
    
class ProductCostMethod(orm.Model):
    """ Product cost method
    """    
    _name = 'product.cost.method'
    _description = 'Cost method'
    
    _columns = {
        'name': fields.char('Rule', size=64, required=False),        
        'category': fields.selection([
            ('company', 'F / Company (base: supplier cost)'),
            ('customer', 'F / Customer (base: company cost)'),
            ('pricelist', 'Pricelist (base: customer cost)'),
            ], 'Category', 
            help='Used for get the cost to update, cost f/company, f/customer'
                'pricelist'),
        'transport_id': fields.many2one(
            'product.cost.transport', 'Transport'),
        'note': fields.text('Note'),
        }

    _defaults = {
        'category': lambda *x: 'company',            
        }

class ProductCostRule(orm.Model):
    """ Product cost rule
    """    
    _name = 'product.cost.rule'
    _description = 'Cost rule'
    _order = 'sequence,id'
    
    _columns = {
        'sequence': fields.integer('Sequence'),
        'name': fields.char('Description', size=64, required=False),        
        'method_id': fields.many2one(
            'product.cost.method', 'Method', ondelete='cascade'),
        'operation': fields.selection([
            ('discount', 'Discount % (-)'),
            ('duty', 'Duty % (+)'),
            ('exchange', 'Exchange (x)'),
            ('transport', 'Tranport (Vol. x transport)'),
            ('recharge', 'Recharge % (+)'),], 'Operation', required=True,
            help='Operation type set the base for operation and type of '
                'operator and also the sign'),
        'mode': fields.selection([
            ('fixed', 'Fixed'),
            ('percentual', 'Percentual'),
            ('multi', 'Multi percentual'),
            ], 'Cost mode', required=True),
        'value': fields.float(
            'Value', digits_compute=dp.get_precision('Product Price')),
        'text_value': fields.char('Text value', size=30, 
            help='Used for multi discount element'),
        'note': fields.char('Note', size=80),
        }
        
    _defaults = {
        'operation': lambda *x: 'recharge',
        'mode': lambda *x: 'percentual',
        }    

class ProductCostMethod(orm.Model):
    """ Product cost method
    """    
    _inherit = 'product.cost.method'
    
    _columns = {
        'rule_ids': fields.one2many('product.cost.rule', 'method_id', 'Rule'), 
        }

class ProductProduct(orm.Model):
    """ Model name: Product Product
    """    
    _inherit = 'product.product'

    # Utility: 
    def get_duty_product_rate(self, cr, uid, duty, country_id, context=None):
        ''' Utility for return duty range from duty browse category and
            country ID of first supplier
        '''
        for country in duty.tax_ids:
            if country.id == country_id:
                return country.tax
        return 0.0
    # -------------------------------------------------------------------------
    #                           Compute method:
    # -------------------------------------------------------------------------
    def get_product_cost_value(self, cr, uid, ids, 
            block='company', context=None):
        ''' Utility for generate cost for product template passed
            product: browse obj for product
            field: name of field that idendify cost method
        '''
        # Database for speed up search:
        duty = {} # database of first supplier duty
        
        for product in self.browse(cr, uid, ids, context=context):
            # Reset variable used:
            calc = ''
            error = ''
            supplier_id = product.first_supplier_id
            country_id = product.first_supplier_id.country_id
            
            # -----------------------------------------------------------------
            #         Get parameter depend on block selected:
            # -----------------------------------------------------------------
            if block == 'company':
                total = product.supplier_cost
                result_field = 'standard_price'
                calc_field = 'company_calc'
                error_field = 'company_calc_error'
            elif block == 'customer':
                total = product.standard_price
                result_field = 'customer_price'
                calc_field = 'customer_calc'
                error_field = 'customer_calc_error'
            if block == 'pricelist':
                total = product.customer_cost
                result_field = 'price_lst'
                calc_field = 'pricelist_calc'
                error_field = 'pricelist_calc_error'
            else:
                self.write(cr, uid, product.id, {
                    error_field: _('Block selection error: %s') % block,
                    }, context=context)
                continue

            if not total:
                self.write(cr, uid, product.id, {
                    error_field: _('Base price is empty (%s)') % block,
                    }, context=context)
                continue
                
            # -----------------------------------------------------------------
            #                  Process all the rules:    
            # -----------------------------------------------------------------
            for rule in product.__getattribute__('%s_method_id' % block):
                
                # Rule parameter (for readability):
                value = rule.value
                mode = rule.mode
                
                # -------------------------------------------------------------
                #                       DISCOUNT RULE:
                # -------------------------------------------------------------
                if rule.mode == 'discount':
                    pass                

                # -------------------------------------------------------------
                #                          DUTY RULE:
                # -------------------------------------------------------------
                elif rule.mode == 'duty':
                    # -------------------------------------
                    # Check mandatory fields for duty calc:
                    # -------------------------------------
                    if not supplier_id: 
                        error += _('''
                        <p><font color="red">
                            First supplier not found!</font>
                        </p>''')
                        continue # next rule
                        
                    if not country_id: 
                        error += _('''
                        <p><font color="red">
                            Country for first supplier not found!</font>
                        </p>''''<p>')
                        continue # next rule

                    duty = product.duty_id                    
                    # Check duty category presence:
                    if not duty: 
                        error += _('''
                        <p><font color="yellow">
                            Duty category not found!</font>
                        </p>''')
                        #continue # it's a warning!!!
                    
                    # Get duty rate depend on supplier country     
                    if supplier_id not in duty:
                        duty[supplier_id] = self.get_duty_product_rate(
                            cr, uid, duty, country_id, 
                            context=context)
                    duty_rate = duty[supplier_id]
    
                    # Check duty rate value (:
                    if not duty: 
                        error += _('Duty rate not found!')
                        continue # next rule
                    
                    duty_value = total * duty_rate
                    total += duty_value
                    calc += '<tr><td>%s</td><td></td><td>+ %s</td></tr>' % (
                        _('Duty rate=%s (Category: %s Country: %s') % (
                            product.duty_id.name,
                            product.first_supplier_id.country_id.name,
                            ),
                        '%s x %s' % (total, duty_rate),
                        duty_value,
                        )    

                # -------------------------------------------------------------
                #                         EXCHANGE RULE:
                # -------------------------------------------------------------
                elif rule.mode == 'exchange':
                    pass

                # -------------------------------------------------------------
                #                         TRANSPORT RULE:
                # -------------------------------------------------------------
                elif rule.mode == 'transport':
                    pass                

                # -------------------------------------------------------------
                #                          RECHARGE RULE:
                # -------------------------------------------------------------
                elif rule.mode == 'recharge':
                    pass                
                
            # -----------------------------------------------------------------
            #                     Write data in product:
            # -----------------------------------------------------------------
            self.write(cr, uid, product.id, {
                result_field: total,
                calc_field: _('''
                    <table>
                        <tr>
                            <th>Description</th>
                            <th>Calculation</th>
                            <th>Subtotal</th>
                        </tr>%s
                    </table>''') % calc, # embed in table
                error_field: error,                    
                }, context=context)                
        return True
    
    # 3 Button:
    def calculate_cost_method_company(self, cr, uid, ids, context=None):
        ''' Button calculate
        '''
        self.get_product_cost_value(cr, uid, product, 
            block='company', context=context)
        return True

    def calculate_cost_method_customer(self, cr, uid, ids, context=None):
        ''' Button calculate
        '''
        self.get_product_cost_value(cr, uid, product, 
            block='customer', context=context)
        return True

    def calculate_cost_method_pricelist(self, cr, uid, ids, context=None):
        ''' Button calculate
        '''
        self.get_product_cost_value(cr, uid, product, 
            block='pricelist', context=context)
        return True
        
    
    """def get_campaign_price(self, cost, price, campaign, product, cost_type):
        # ---------------------------------------------------------------------
        # Product cost generation:
        # ---------------------------------------------------------------------
        total = 0.0
        for rule in cost_type.rule_ids:
            # Read rule parameters
            sign = rule.sign
            base = rule.base
            mode = rule.mode
            value = rule.value
            text_value = rule.text_value
            
            # -----------
            # Sign coeff:
            # -----------
            if sign == 'minus':
                sign_coeff = -1.0  
            else:
                sign_coeff = 1.0
                
            # ----------------
            # Base evaluation:
            # ----------------
            if base == 'previous':
                base_value = total
            elif base == 'cost':
                base_value = cost
                if not total: # Initial setup depend on first rule
                    total = cost 
            elif base == 'price':
                base_value = price
                if not total: # Initial setup depend on first rule
                    total = price
            #elif base == 'volume':
            #    base_value = (
            #        product.volume / campaign.volume_total)                    
            else:
                _logger.error('No base value found!!!')                
                # TODO raise error?        

            # -----------
            # Value type:
            # -----------
            if mode == 'fixed':
                total += sign_coeff * value
                continue # Fixed case only increment total no other operations                
            elif mode == 'multi':
                # TODO check sign for multi discount value (different from revenue)
                # Convert multi discount with value
                value = sign_coeff * partner_pool.format_multi_discount(
                    text_value).get('value', 0.0)
            elif mode == 'percentual':
                value *= sign_coeff
            else:    
                _logger.error('No mode value found!!!')
                # TODO raise error?        
                    
            if not value:
                _logger.error('Percentual value is mandatory!')
                pass
            total += base_value * value / 100.0

        # --------------------------------
        # General cost depend on campaign:    
        # --------------------------------
        volume_cost = campaign.volume_cost
        discount_scale = campaign.discount_scale
        revenue_scale = campaign.revenue_scale
        
        # TODO correct!!!!:
        if volume_cost:        
            total += total * product.qty * (
                product.volume / campaign.volume_total)
            # TODO use heigh, width, length 
            # TODO use pack_l, pack_h, pack_p
            # TODO use packaging dimension?
            
        if discount_scale:
            discount_value = partner_pool.format_multi_discount(
                discount_scale).get('value', 0.0)
            total -= total * discount_value / 100.0

        if revenue_scale:
            revenue_value = partner_pool.format_multi_discount(
                revenue_scale).get('value', 0.0)
            total += total * revenue_value / 100.0
            
        # TODO extra recharge:
        return total"""

class ProductTemplate(orm.Model):
    """ Model name: ProductTemplate
    """    
    _inherit = 'product.template'
    
    _columns = {
        # 3 Method:
        'company_method_id': fields.many2one(
            'product.cost.method', 'Company Method'),
        'customer_method_id': fields.many2one(
            'product.cost.method', 'Customer Method'),
        'pricelist_method_id': fields.many2one(
            'product.cost.method', 'Pricelist Method'),
        # 3 Text result:    
        'company_calc': fields.text(
            'Company calc', readonly=True, widget='html'),
        'customer_calc': fields.text(
            'Customer calc', readonly=True, widget='html'),    
        'pricelist_calc': fields.text(
            'Pricelist calc', readonly=True, widget='html'),    
        # 3 Text calc error:
        'company_calc_error': fields.text(
            'Company calc error', readonly=True, widget='html'),
        'customer_calc_error': fields.text(
            'Customer calc error', readonly=True, widget='html'),
        'pricelist_calc_error': fields.text(
            'Pricelist calc error', readonly=True, widget='html'),
        
        'supplier_cost': fields.float('Supplier cost', 
            digits_compute=dp.get_precision('Product Price'), 
            help='Supplier cost (pricelist cost, f/company)'),
        'customer_cost': fields.float('Customer cost', 
            digits_compute=dp.get_precision('Product Price'), 
            help='Customer cost (base for calculate goods f/customer)'),
        }

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: