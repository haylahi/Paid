# -*- coding: utf-8 -*-
import werkzeug
from contextlib import closing
import logging
_logger = logging.getLogger(__name__)
import os
import shutil
import subprocess

import openerp
import openerp.http as http
from openerp.http import request
from openerp import SUPERUSER_ID

class SaasMultiDB(http.Controller):

    @http.route('/try/package', type="http", auth="public", website=True)
    def saas_package(self, **kw):
        """Webpage that let's a user select a template database / package"""
        template_databases = request.env['saas.template.database'].search([])
        return http.request.render('saas_multi_db.saas_choose_package', {'template_databases': template_databases})

    @http.route('/try/details', type="http", auth="public", website=True)
    def saas_info(self, **kw):
        """Webpage for users to enter details about thier saas setup"""

        values = {}
	for field_name, field_value in kw.items():
	    values[field_name] = field_value
	    
	template_database = request.env['saas.template.database'].browse(int(values['templatedb']))
        return http.request.render('saas_multi_db.saas_submit', {'template_database': template_database})

    @http.route('/saas/createdb', type="http", auth="public")
    def saas_create_datadb(self, **kwargs):
        """Creates and sets up the new database"""
        
        values = {}
	for field_name, field_value in kwargs.items():
	    values[field_name] = field_value
	
	email = values["email"]
	password = values["password"]
	system_name = values["system_name"]
	person_name = values["person_name"]
        demo = False

	#get the template database
	template_database = request.env['saas.template.database'].browse(int(values["package"]))
	chosen_template = template_database.database_name + "_clone"

        #Check if this email has been used to create a SAAS system before
        if request.env['res.users'].sudo().search_count([('login','=',email)]) > 0:
            return "Email already used to create SAAS system"
        
        #Create SAAS Partner            
        partner = request.env['res.partner'].sudo().create({'name':person_name, 'email':email, 'saas_partner': True, 'saas_database': system_name})
	    
	#Add this database to the saas list
	request.env['saas.database'].create({'name':system_name, 'partner_id': partner.id, 'login': email, 'password': password})

        #Create a new instance just for the user
        #if template_database.create_instance:
        #    subprocess.call("/root/odoo_install.sh", system_name, "9000")

        #Create the new database from the template database, disconnecting any users that might be using the template database
        db_original_name = chosen_template
        db_name = system_name
        openerp.sql_db.close_db(db_original_name)
        db = openerp.sql_db.db_connect('postgres')        
        with closing(db.cursor()) as cr:
            cr.autocommit(True)     # avoid transaction block
            self._drop_conn(cr, db_original_name)
            
            #Check if the database already exists
	    cr.execute("SELECT datname FROM pg_database WHERE datname = %s",(db_name,))
	    if cr.fetchall():
	        return "Database already exists"
	        
            cr.execute("""CREATE DATABASE "%s" ENCODING 'unicode' TEMPLATE "%s" """ % (db_name, db_original_name))

        from_fs = openerp.tools.config.filestore(db_original_name)
        to_fs = openerp.tools.config.filestore(db_name)
        
        if os.path.exists(from_fs) and not os.path.exists(to_fs):
            shutil.copytree(from_fs, to_fs)

        #connect to the newly created database
	db = openerp.sql_db.db_connect(db_name)

        #Create new registry
        registry = openerp.modules.registry.RegistryManager.new(system_name, demo, None, update_module=True)

	#Update the saas user's name, email, login and password
	with closing(db.cursor()) as cr:
	    cr.autocommit(True)     # avoid transaction block
	    saas_user = registry['ir.model.data'].get_object(cr, SUPERUSER_ID, 'saas_multi_db_client', 'saas_user')
	    saas_user.write({'name':person_name, 'email':email, 'login':email, 'password':password})
        
        #Auto login causing issues?!?
	request.session.authenticate(system_name, email, '')
	#request.session.authenticate(system_name, email, password)
        
        return werkzeug.utils.redirect("http://" + system_name + "." + request.httprequest.host )
	#return http.local_redirect('/web/')
        
    def _drop_conn(self, cr, db_name):
        # Try to terminate all other connections that might prevent
        # dropping the database
        try:
            # PostgreSQL 9.2 renamed pg_stat_activity.procpid to pid:
            # http://www.postgresql.org/docs/9.2/static/release-9-2.html#AEN110389
            pid_col = 'pid' if cr._cnx.server_version >= 90200 else 'procpid'
    
            cr.execute("""SELECT pg_terminate_backend(%(pid_col)s)
                          FROM pg_stat_activity
                          WHERE datname = %%s AND
                                %(pid_col)s != pg_backend_pid()""" % {'pid_col': pid_col},
                       (db_name,))
        except Exception:
            pass