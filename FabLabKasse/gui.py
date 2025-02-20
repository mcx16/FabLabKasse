#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# FabLabKasse, a Point-of-Sale Software for FabLabs and other public and trust-based workshops.
# Copyright (C) 2015  Julian Hammer <julian.hammer@fablab.fau.de>
#                     Maximilian Gaukler <max@fablab.fau.de>
#                     Patrick Kanzler <patrick.kanzler@fablab.fau.de>
#                     Timo Voigt <timo@fablab.fau.de>
#
# This program is free software: you can redistribute it and/or modify it under the terms of the GNU
# General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
# even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this program. If not,
# see <http://www.gnu.org/licenses/>.

from __future__ import print_function
from __future__ import absolute_import
import sys
import re
import locale
import logging
import datetime
import os
import string
import time
from decimal import Decimal, DecimalException
from qtpy import QtGui, QtCore, QtWidgets
import functools
from configparser import Error as ConfigParserError

from .libs.pxss import pxss
from FabLabKasse.UI.GUIHelper import (
    resize_table_columns,
    connect_button,
    connect_button_to_lineedit,
)

# import UI
from .UI.uic_generated.Kassenterminal import Ui_Kassenterminal
from .UI.PaymentMethodDialogCode import PaymentMethodDialog
from .UI.KeyboardDialogCode import KeyboardDialog

from . import scriptHelper


if __name__ == "__main__":
    # switching to german:
    locale.setlocale(locale.LC_ALL, "de_DE.UTF-8")

    cfg = scriptHelper.getConfig()


def shopping_backend_factory(backendname):
    """load a Shopping Backend according to backendname
    :param backendname: name of backend
    :return: ShoppingBackend instance
    :rtype: shopping.backend.abstract.AbstractShoppingBackend
    """
    assert backendname in ["dummy", "legacy_offline_kassenbuch"]
    # TODO there are probably nicer forms than the following import hack-magic
    shopping_backend_module = importlib.import_module(
        "FabLabKasse.shopping.backend." + backendname
    )
    return shopping_backend_module.ShoppingBackend


from .shopping.backend.abstract import ProductNotFound, PrinterError
import importlib

if __name__ == "__main__":
    backendname = cfg.get("backend", "backend")
else:
    print(
        "WARNING: gui.py: fake import for documentation active, instead of conditional import of backend"
    )
    backendname = "dummy"

ShoppingBackend = shopping_backend_factory(backendname)


def format_decimal(value):
    """convert float, Decimal, int to a string with a locale-specific decimal point"""
    return str(value).replace(".", locale.localeconv()["decimal_point"])


class Kassenterminal(Ui_Kassenterminal, QtWidgets.QMainWindow):
    def __init__(self):
        logging.info("GUI startup")
        Ui_Kassenterminal.__init__(self)
        QtWidgets.QMainWindow.__init__(self)

        self.setupUi(self)
        # maximize window - WORKAROUND because showMaximized() doesn't work
        # when a default geometry is set in the Qt designer file
        QtCore.QTimer.singleShot(
            0, lambda: self.setWindowState(QtCore.Qt.WindowMaximized)
        )
        self.shoppingBackend = ShoppingBackend(cfg)
        """time when the program was started, used for auto-restart"""
        self.startup_time = time.monotonic()

        # TODO check at startup for all cfg.get* calls
        cfg.getint("payup_methods", "overpayment_product_id")
        cfg.getint("payup_methods", "payout_impossible_product_id")

        # Configure table views
        for table in [self.table_products, self.table_order]:
            # forbid resizing columns
            table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
            # forbid changing column order
            table.verticalHeader().setSectionsMovable(False)

            table.horizontalHeader().setSectionResizeMode(
                QtWidgets.QHeaderView.Fixed
            )  # forbid resizing columns
            table.horizontalHeader().setSectionsMovable(
                False
            )  # forbid changing column order
            # Disable editing on table
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        # Configure kinetic scrolling (TODO use QScroller.TouchGesture? check what if our system generates touch events)
        for scrollable_component in [
            self.table_products,
            self.table_order,
            self.list_categories,
        ]:
            QtWidgets.QScroller.grabGesture(
                scrollable_component.viewport(),
                QtWidgets.QScroller.LeftMouseButtonGesture,
            )
            scrollable_component.setVerticalScrollMode(
                QtWidgets.QAbstractItemView.ScrollPerPixel
            )

        # Connect up the buttons. (lower half)
        # connect buttons like this, but in a for loop:
        # self.pushButton_0.clicked.connect(lambda x: self.insertIntoLineEdit("0"))
        # self.pushButton_1.clicked.connect(lambda x: self.insertIntoLineEdit("1"))
        # ...
        for i in list(range(10)) + list(string.ascii_lowercase):
            connect_button_to_lineedit(self, i)
        # TODO setFocusPolicy none on push buttons.
        self.pushButton_backspace.clicked.connect(self.backspaceLineEdit)
        self.pushButton_delete.clicked.connect(self.buttonDelete)
        self.pushButton_OK.clicked.connect(self.on_ok_clicked)
        self.pushButton_decimal_point.clicked.connect(
            lambda x: self.insertIntoLineEdit(locale.localeconv()["decimal_point"])
        )
        self.pushButton_decimal_point.setText(locale.localeconv()["decimal_point"])
        self.pushButton_payup.clicked.connect(self.payup)
        self.pushButton_clearCart.clicked.connect(self._clear_cart)

        # Connect keyboard buttons
        # like this but in a for loop:
        # self.pushButton_q.clicked.connect(lambda x: self.insertIntoLineEdit_Suche("q"))
        # self.pushButton_w.clicked.connect(lambda x: self.insertIntoLineEdit_Suche("w"))
        # ...
        # self.pushButton_a_0.clicked.connect(lambda x: self.insertIntoLineEdit_Suche("0"))
        # self.pushButton_a_1.clicked.connect(lambda x: self.insertIntoLineEdit_Suche("1"))
        def connect_button_from_keyboard(btn_suffix):
            # connect self.pushButton_<btn_suffix> to self.insertIntoLineEdit_Suche(<text of the button>)
            btn = getattr(self, "pushButton_" + str(btn_suffix))
            connect_button(btn, self.insertIntoLineEdit_Suche)

        for i in (
            list(string.ascii_lowercase)
            + ["oe", "ae", "ue", "sz", "minus", "dot", "komma"]
            + ["a_" + str(i) for i in range(10)]
        ):
            connect_button_from_keyboard(i)
        self.pushButton_space.clicked.connect(
            lambda x: self.insertIntoLineEdit_Suche(" ")
        )
        self.pushButton_backspace_3.clicked.connect(self.backspaceLineEdit_Suche)
        self.pushButton_enter.clicked.connect(self.searchItems)

        self.lineEdit_Suche.focused.connect(self.on_lineEdit_search_clicked)
        self.lineEdit_Suche.clicked.connect(
            self.on_lineEdit_search_clicked
        )  # this is necessary because in rare cases focused() is not emitted

        self.lineEdit_Suche.cursorPositionChanged.connect(
            lambda x: self.lineEdit_Suche.end(False)
        )  # move cursor to end whenever it is moved
        self.lineEdit.cursorPositionChanged.connect(
            lambda x: self.lineEdit.end(False)
        )  # move cursor to end whenever it is moved

        # Search if anything gets typed
        self.lineEdit_Suche.textEdited.connect(lambda x: self.searchItems(preview=True))

        # Search (and get rid of keyboard) on return key
        self.lineEdit_Suche.returnPressed.connect(lambda: self.searchItems())

        # Connect up the buttons. (upper half)
        self.pushButton_start.clicked.connect(self.on_start_clicked)

        # Connect category list to change category function
        self.list_categories.clicked.connect(self.on_category_clicked)

        # Connect lineEdit to produce useful strings
        # use textEdited instead of textChanged because this ignores events caused by setText()
        self.lineEdit.textEdited.connect(self.on_lineEdit_changed)

        # Connect lineEdit.returnPressed to be the same as clicking on ok button
        self.lineEdit.returnPressed.connect(self.on_ok_clicked)

        # Add product to cart when selecting a product from the table
        self.table_products.clicked.connect(self.on_product_clicked)

        # Connect to table_order changed selection
        self.table_order.clicked.connect(
            lambda x: self.on_order_clicked()
        )  # lambda is necessary because we don't want the second (default) parameter to be set

        # Disable vertical header on table_order
        self.table_order.verticalHeader().setVisible(False)

        # Shopping carts/orders
        self.updateOrder()

        # currently selected product group
        self.current_category = self.shoppingBackend.get_root_category()

        # Initialize categories and products later, after resize events are done
        QtCore.QTimer.singleShot(0, self.updateProductsAndCategories)

        # Give focus to lineEdit
        self.lineEdit.setFocus()

        # start and configure idle reset for category view
        if cfg.has_option("idle_reset", "enabled"):
            if cfg.getboolean("idle_reset", "enabled"):
                idle_threshold = 1800
                if cfg.has_option("idle_reset", "threshold_time"):
                    idle_threshold = cfg.getint("idle_reset", "threshold_time")
                self.idleTracker = pxss.IdleTracker(1000 * idle_threshold)
                (idle_state, _, _) = self.idleTracker.check_idle()
                if idle_state == "disabled":
                    self.idleCheckTimer.stop()
                    logging.warning(
                        "Automatic reset on idle is disabled since idleTracker returned `disabled`."
                    )
                self.idleCheckTimer = QtCore.QTimer()
                self.idleCheckTimer.setInterval(
                    int(idle_threshold * 1000 / 2)
                )  # to avoid spamming the log, we only check in long intervals
                self.idleCheckTimer.timeout.connect(self._reset_if_idle)
                self.idleCheckTimer.start()

    def askUser(self, question):
        """ask the user a question and return whether she agreed."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "Message",
            question,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return reply == QtWidgets.QMessageBox.Yes

    def restart(self):
        """
        Restart menu clicked
        """
        # Ask if restart is okay
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setText("Ein Neustart löscht den aktuellen Warenkorb! Fortsetzen?")
        dialog.addButton(QtWidgets.QMessageBox.Cancel)
        dialog.addButton(QtWidgets.QMessageBox.Ok)
        dialog.setDefaultButton(QtWidgets.QMessageBox.Ok)
        dialog.setEscapeButton(QtWidgets.QMessageBox.Cancel)
        if dialog.exec_() != QtWidgets.QMessageBox.Ok:
            return

        # Choose restart type
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setText("Was soll passieren?")
        dialog.addButton("Produkte\nneu laden", QtWidgets.QMessageBox.YesRole)
        rebootButton = dialog.addButton("Neustart", QtWidgets.QMessageBox.YesRole)
        shutdownButton = dialog.addButton(
            "Herunterfahren", QtWidgets.QMessageBox.YesRole
        )
        dialog.addButton(QtWidgets.QMessageBox.Cancel)
        if dialog.exec_() == QtWidgets.QMessageBox.Cancel:
            return

        result = dialog.clickedButton()
        restartType = "restart"
        # trigger restart/reboot ('sudo reboot' will be executed by run.py)
        if result == rebootButton:
            restartType = "reboot"
        elif result == shutdownButton:
            restartType = "shutdown"
        self.do_restart(restartType)

    def do_restart(self, restart_type="restart"):
        """
        trigger restart/shutdown/reboot without further confirmation

        :param restart_type: "restart" (Software restart), "reboot" (OS reboot) or "shutdown" (OS shutdown)

        The actual reboot/shutdown will be performed by run.py.
        For "restart", the program exits, terminating the X11 session. It will then be restarted by the desktop session manager after auto-login.
        """
        if restart_type == "reboot":
            with open("./reboot-now", "w") as f:
                f.write("42")
        elif restart_type == "shutdown":
            with open("./shutdown-now", "w") as f:
                f.write("42")
        logging.info("exiting because of restart request")
        self.close()

    def autoRebootOnUpdates(self):
        """
        Automatically restart/reboot if required:

        - reboot when /var/run/reboot-required is present (created by unattended-upgrades)
        - restart after 48 hours to ensure the product list is up to date

        See do_restart() for implementation details.

        This function does not care about the current UI state. Therefore it should not be called, e.g., in the middle of a payment.

        Assumption: This function is only called directly after a successful payment, before the user has any chance to add new things to the cart or even start a new payment.
        """

        # Determine if reboot/restart is needed
        if os.path.isfile("/var/run/reboot-required"):
            restart_type = "reboot"
        elif time.monotonic() > self.startup_time + 48 * 3600:
            restart_type = "restart"
        else:
            restart_type = None

        if restart_type is None:
            return

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.setText(
            "Danke für deine Bezahlung.\nDie Kasse wird jetzt für ein Update neu gestartet."
        )
        dialog.addButton(QtWidgets.QMessageBox.Cancel)
        dialog.addButton(QtWidgets.QMessageBox.Ok)
        dialog.setDefaultButton(QtWidgets.QMessageBox.Ok)
        dialog.setEscapeButton(QtWidgets.QMessageBox.Cancel)
        if dialog.exec_() != QtWidgets.QMessageBox.Ok:
            return

        self.do_restart(restart_type)

    def serviceMode(self):
        """was the service mode enabled recently? check and disable again"""

        def checkServiceModeEnabled(showErrorMessage=True):
            # for enabling the service mode, the file ./serviceModeEnabled needs to be newer than 30sec
            try:
                lastEnabled = datetime.datetime.utcfromtimestamp(
                    os.lstat("./serviceModeEnabled").st_mtime
                )
            except OSError:
                if showErrorMessage:
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Ups",
                        "Servicemodus nicht aktiviert\n Bitte ./enableServiceMode ausführen",
                    )
                return

            delta = datetime.timedelta(0, 30, 0)
            now = datetime.datetime.utcnow()
            if not (now - delta < lastEnabled < now):
                if showErrorMessage:
                    QtWidgets.QMessageBox.warning(
                        self, "Hey", "Zu spät, Aktivierung gilt nur 30sec."
                    )
                return False
            os.unlink("./serviceModeEnabled")
            return True

        if not checkServiceModeEnabled():
            return

        dialog = QtWidgets.QMessageBox(self)
        if not self.askUser("Automat sperren?"):
            return
        while True:
            dialog = QtWidgets.QMessageBox(self)
            dialog.setText(
                "Der Automat ist wegen Wartungsarbeiten für kurze Zeit nicht verfügbar.\nBitte wende dich zur Bezahlung an einen Betreuer.\n\n(zum Entsperren: ./enableServiceMode ausführen und OK drücken)"
            )
            dialog.addButton(QtWidgets.QMessageBox.Ok)
            dialog.setStyleSheet("background-color:red; color:white; font-weight:bold;")
            dialog.exec_()
            if checkServiceModeEnabled(showErrorMessage=False):
                return

    def changeProductCategory(self, category):
        # if search was done before, switch from keyboard to basket view
        self.leaveSearch()
        self.current_category = category
        self.updateProductsAndCategories()

    def on_start_clicked(self):
        """resets the categories to the root element

        * leaves current search
        * sets current category to the root element
        * triggers the update of the category-view
        """
        self.leaveSearch()
        self.current_category = self.shoppingBackend.get_root_category()
        self.updateProductsAndCategories()

    def on_category_clicked(self, index=None):
        self.current_category = index.data(QtCore.Qt.UserRole + 1)

        self.leaveSearch()
        self.updateProductsAndCategories()

    def on_category_path_button_clicked(self):
        source = self.sender()
        self.current_category = source.category_id
        self.updateProductsAndCategories()

    def _add_to_category_path(self, name, categ_id, bold):
        """add button with text 'name' and callback for opening category categ_id to the category path"""
        # l = Qt.QLabel()
        # l.setText(u"►")
        # self.layout_category_path.addWidget(l)
        button = QtWidgets.QPushButton(" ► " + name)
        button.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred
        )
        if categ_id is not None:
            button.category_id = categ_id
            button.clicked.connect(self.on_category_path_button_clicked)
        self.layout_category_path.addWidget(button)
        if bold:
            # set (last) button to bold
            font = button.font()
            font.setBold(True)
            button.setFont(font)

    def updateProductsAndCategories(
        self, categories=None, products=None, category_path=None
    ):
        """update models for products, categories, and the category path

        categories: list(Category), products: list(Product), category_path: list(Category) or a string to display one non-clickable button"""
        if categories is None:
            categories = self.shoppingBackend.get_subcategories(self.current_category)

        if products is None:
            products = self.shoppingBackend.get_products(self.current_category)

        if category_path is None:
            category_path = self.shoppingBackend.get_category_path(
                self.current_category
            )

        categ_model = QtGui.QStandardItemModel(len(categories), 1)
        for i, c in enumerate(categories):
            item = QtGui.QStandardItem(c.name)
            item.setData(c.categ_id)
            categ_model.setItem(i, 0, item)
        self.list_categories.setModel(categ_model)

        # Clear all buttons in layout_category_path
        for i in range(self.layout_category_path.count()):
            self.layout_category_path.itemAt(i).widget().setVisible(False)
            self.layout_category_path.itemAt(i).widget().deleteLater()

        if isinstance(category_path, str):
            # special case: display a string
            # used for "Search Results"
            self._add_to_category_path(name=category_path, categ_id=None, bold=True)
        else:
            # Add buttons to layout_category_path
            for c in category_path[:-1]:
                self._add_to_category_path(c.name, c.categ_id, bold=False)
            # make last button with bold text
            if category_path:
                self._add_to_category_path(
                    category_path[-1].name, category_path[-1].categ_id, bold=True
                )

        # set "all products" button to bold if the root category is selected
        font = self.pushButton_start.font()
        font.setBold(len(category_path) == 0)
        self.pushButton_start.setFont(font)

        prod_model = QtGui.QStandardItemModel(len(products), 5)
        for i, p in enumerate(products):
            prod_id = QtGui.QStandardItem(str(p.prod_id))
            prod_id.setData(p.prod_id)
            # prod_id.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            light_font = QtGui.QFont()
            light_font.setPointSize(10)
            prod_id.setFont(light_font)
            prod_model.setItem(i, 0, prod_id)

            name = QtGui.QStandardItem(p.name)
            prod_model.setItem(i, 1, name)

            loc = QtGui.QStandardItem()
            loc.setText(p.location)
            prod_model.setItem(i, 2, loc)

            uos = QtGui.QStandardItem(p.unit)
            prod_model.setItem(i, 3, uos)

            price = QtGui.QStandardItem(self.shoppingBackend.format_money(p.price))
            prod_model.setItem(i, 4, price)

        prod_model.setHorizontalHeaderItem(0, QtGui.QStandardItem("Nr"))
        prod_model.setHorizontalHeaderItem(1, QtGui.QStandardItem("Artikel"))
        prod_model.setHorizontalHeaderItem(2, QtGui.QStandardItem("Lagerort"))
        prod_model.setHorizontalHeaderItem(3, QtGui.QStandardItem("Einheit"))
        prod_model.setHorizontalHeaderItem(4, QtGui.QStandardItem("Preis"))

        self.table_products.setModel(prod_model)
        # adjust height: large enough for precise touching, chosen such that the last item is "half cut off" to make it obvious that you need to scroll further
        for i in range(len(products)):
            self.table_products.setRowHeight(i, 42)

        # Change column width to useful values
        # needs to be delayed so that resize events for the scrollbar happens first, otherwise it reports a scrollbar width of 100px at the very first call
        QtCore.QTimer.singleShot(
            0,
            functools.partial(
                resize_table_columns, self.table_products, [0.7, 5, 2.5, 2, 1]
            ),
        )

        # give back focus to PLU entry
        self.lineEdit.setFocus()

    def addOrderLine(self, prod_id, qty=0):
        logging.debug(
            "addOrderLine "
            + str(prod_id)
            + " "
            + str(self.shoppingBackend.get_current_order())
        )
        if self.shoppingBackend.get_current_order() is None:
            order = self.shoppingBackend.create_order()
            self.shoppingBackend.set_current_order(order)
        text = None
        if self.shoppingBackend.product_requires_text_entry(prod_id):
            text = KeyboardDialog.askText("Kommentar:", parent=self)
            if text is None:
                return
        self.shoppingBackend.add_order_line(prod_id, qty, comment=text)
        self.updateOrder(selectLastItem=True)
        return

    def on_product_clicked(self):
        # delete all zero-quantity products
        for line in list(
            self.shoppingBackend.get_order_lines()
        ):  # cast to list so that iterator is not broken when deleting items
            if line.qty == 0 and line.delete_if_zero_qty:
                self.shoppingBackend.delete_order_line(line.order_line_id)

        # Retrieve selected product from table
        idx = self.table_products.currentIndex()
        row = idx.row()
        model = idx.model()
        if model is None:
            return
        prod_id = model.item(row, 0).data()

        # Add selected product to table
        self.addOrderLine(prod_id)

        # show basket, but also keep search results visible
        self.leaveSearch(keepResultsVisible=True)

    def payup(self):
        """ask the user to pay the current order.
        returns True if the payment was successful, False or None otherwise.
        """
        if self.shoppingBackend.get_current_order() is None:
            # There is no order. Thus payup does not make sense.
            return

        # rounding must take place in shoppingBackend
        total = self.shoppingBackend.get_current_total()
        if total == 0:
            return
        assert isinstance(total, Decimal)
        assert total >= 0
        assert (
            total % Decimal("0.01") == 0
        ), "current order total is not rounded to cents"

        logging.info(
            f"starting payment for cart: {self.shoppingBackend.get_order_lines()}"
        )

        if total > 250:
            # cash-accept is unlimited, but dispense is locked to maximum 200€ hardcoded. Limit to
            # a sensible amount here
            msgBox = QtWidgets.QMessageBox(self)
            msgBox.setText(
                "Bezahlungen über 250 Euro sind leider nicht möglich. Bitte wende "
                + "dich an einen Betreuer, um es per Überweisung zu zahlen."
            )
            msgBox.exec_()
            return

        # Step 1: Choose payment method
        pm_diag = PaymentMethodDialog(parent=self, cfg=cfg, amount=total)
        paymentmethod = None

        if not pm_diag.exec_():
            # Has cancled request for payment method selection
            return

        paymentmethod = pm_diag.getSelectedMethodInstance(
            self, self.shoppingBackend, total
        )
        logging.info(
            f"started payment of {self.shoppingBackend.format_money(total)} with {str(type(paymentmethod))}"
        )
        paymentmethod.execute_and_store()
        logging.info(f"payment ended. result: {paymentmethod}")
        assert paymentmethod.amount_paid >= 0

        # Receipt printing
        if cfg.getboolean("general", "receipt"):
            if paymentmethod.print_receipt == "ask":
                paymentmethod.print_receipt = self.askUser("Brauchst du eine Quittung?")
            if paymentmethod.print_receipt:
                try:
                    # TOOD show amount returned on receipt (needs some rework, because it is not yet stored in the order and so we cannot re-print receipts)
                    self.shoppingBackend.print_receipt(paymentmethod.receipt_order_id)
                except PrinterError as e:
                    try:
                        email = cfg.get("general", "support_mail")
                    except ConfigParserError:
                        email = "einem zuständigen Betreuer"
                    QtWidgets.QMessageBox.warning(
                        self,
                        "Quittung",
                        "Drucker scheint offline zu sein.\n"
                        "Falls du wirklich eine Quittung brauchst, melde dich bei "
                        f"{email} mit Datum, Uhrzeit und Betrag.",
                    )
                    logging.warning(f"printing receipt failed: {repr(e)}")
        if paymentmethod.successful:
            paymentmethod.show_thankyou()
            self.shoppingBackend.set_current_order(None)
            self.updateOrder()
            self.on_start_clicked()
            self.autoRebootOnUpdates()
        return paymentmethod.successful

    def getSelectedOrderLineId(self):
        order_idx = self.table_order.currentIndex()
        if order_idx.model() and order_idx.isValid():
            order_line_id = order_idx.model().item(order_idx.row(), 0).data()
            return order_line_id
        else:
            return None

    def on_order_clicked(self, leave_lineEdit_empty=False):
        order_idx = self.table_order.currentIndex()
        logging.debug("on_order_clicked " + str(order_idx.row()))
        order_line_id = self.getSelectedOrderLineId()
        if order_line_id is not None:
            order_line = self.shoppingBackend.get_order_line(order_line_id)
            self.label_unit.setText(order_line.unit)
        if leave_lineEdit_empty:
            self.lineEdit.setText("")
            self.on_lineEdit_changed()
            return
        if order_line_id is not None:
            self.lineEdit.setText(self.shoppingBackend.format_qty(order_line.qty))
            self.on_lineEdit_changed()

    def insertIntoLineEdit(self, char):
        self.lineEdit.setFocus()
        self.lineEdit.setText(self.lineEdit.text() + char)
        self.on_lineEdit_changed()

    def backspaceLineEdit(self):
        oldtext = self.lineEdit.text()
        if oldtext:
            self.lineEdit.setText(oldtext[:-1])
            self.on_lineEdit_changed()

    def on_lineEdit_changed(self):
        input = self.lineEdit.text()
        # convert comma to dot
        input = input.replace(locale.localeconv()["decimal_point"], ".")
        # Getting rid of all special characters (except for numbers and commas)
        newString = re.sub(r"[^0-9\.]", "", str(input))

        # remove multiple commas and only keep last (last = right most)
        comma_count = newString.count(".")
        if comma_count > 1:
            newString = newString.replace(".", "", comma_count - 1)

        selected_order_line_id = self.getSelectedOrderLineId()  # selected order line

        # switch on the "decimal point" button if
        # the user has not yet entered a decimal point
        # and we are not in PLU entry mode (= no product is currently selected)
        self.pushButton_decimal_point.setEnabled(
            comma_count < 1 and selected_order_line_id is not None
        )

        # Set correctly formated text, if anything changed (preserves cursor position)
        # replace back from dot to comma
        newString = newString.replace(".", locale.localeconv()["decimal_point"])
        newString = newString[0:8]  # limit input length
        if newString != input:
            self.lineEdit.setText(newString)

        # update currently selected product quantity
        qty = self.getLineEditDecimal()

        if selected_order_line_id is not None:
            self.shoppingBackend.update_quantity(selected_order_line_id, qty)
            order_line = self.shoppingBackend.get_order_line(selected_order_line_id)
            if order_line.qty != qty:
                # quantity was rounded up, notify user
                QtWidgets.QToolTip.showText(
                    self.label_unit.mapToGlobal(QtCore.QPoint(0, -30)),
                    f"Eingabe wird auf {format_decimal(order_line.qty)} {order_line.unit} aufgerundet!",
                )
            else:
                QtWidgets.QToolTip.hideText()
            self.updateOrder()
        else:
            # PLU input
            pass

    def buttonDelete(self):
        order_line = self.getSelectedOrderLineId()
        logging.debug("buttonDelete " + str(order_line))
        if order_line is not None:
            self.shoppingBackend.delete_order_line(order_line)
            self.updateOrder()
            self.start_plu_entry()  # update lineEdit_input and label_qty

    def start_plu_entry(self):
        """clear quantity textbox, start entering PLU. This is called e.g. after quantity-entry is finished"""
        # Change to PLU mode by deselecting the order
        self.table_order.setCurrentIndex(QtCore.QModelIndex())
        self.lineEdit.setText("")
        self.label_unit.setText("PLU / Artikelnummer:")
        self.pushButton_decimal_point.setEnabled(False)

    def on_ok_clicked(self):
        # "OK" button pressed
        order_idx = self.table_order.currentIndex()
        plu = str(self.lineEdit.text()).strip()

        if order_idx.isValid():
            # quantity entry is now finished.
            self.start_plu_entry()
        else:
            # PLU mode, because no order_line was selected or order was not yet created
            # only digits are allowed
            try:
                # Add order line and switch to qty mode
                self.addOrderLine(self.shoppingBackend.search_product_from_code(plu))
            except ProductNotFound:
                self.start_plu_entry()

    def getLineEditDecimal(self):
        amount = self.lineEdit.text()
        try:
            qty = Decimal(
                str(amount.replace(locale.localeconv()["decimal_point"], "."))
            )
        except DecimalException:
            qty = Decimal(0)
        return qty

    def updateOrder(self, selectLastItem=False):
        logging.debug("updateOrder")
        # delete sale order if last line was deleted
        if (
            self.shoppingBackend.get_current_order() is not None
            and not self.shoppingBackend.get_order_lines()
        ):
            self.shoppingBackend.delete_current_order()

        # Currently no open cart
        if self.shoppingBackend.get_current_order() is None:
            self.table_order.setModel(QtGui.QStandardItemModel(0, 0))
            self.summe.setText("0,00 €")
            self.pushButton_payup.setEnabled(False)
            self.pushButton_clearCart.setEnabled(False)
            self.start_plu_entry()
            return

        # TODO get_orders() ... and switch between tabs

        # Save row selection and count
        old_selected_row = self.table_order.currentIndex().row()
        old_row_count = self.table_order.model().rowCount()

        self.table_order.update_cart(self.shoppingBackend)

        if selectLastItem:
            # select last line - used when a new line was just added
            self.table_order.selectRow(self.table_order.model().rowCount() - 1)
            self.on_order_clicked(leave_lineEdit_empty=True)
        else:
            new_row_count = self.table_order.model().rowCount()
            if new_row_count == old_row_count:
                self.table_order.selectRow(old_selected_row)

        # Update summe:
        total = self.shoppingBackend.get_current_total()
        self.summe.setText(
            self.shoppingBackend.format_money(self.shoppingBackend.get_current_total())
        )

        # disable "pay now" button on empty bill
        self.pushButton_payup.setEnabled(total > 0)
        self.pushButton_clearCart.setEnabled(True)

    # keyboard search interaction
    def on_lineEdit_search_clicked(self):
        self.stackedWidget.setCurrentIndex(1)

    def insertIntoLineEdit_Suche(self, char):
        self.lineEdit_Suche.setFocus()
        self.lineEdit_Suche.setText(self.lineEdit_Suche.text() + char)
        self.searchItems(preview=True)

    def backspaceLineEdit_Suche(self):
        oldtext = self.lineEdit_Suche.text()
        if oldtext:
            self.lineEdit_Suche.setText(oldtext[:-1])
        self.lineEdit_Suche.setFocus()
        self.searchItems(preview=True)

    # list searched items in product tree
    def searchItems(self, preview=False):
        searchstr = str(self.lineEdit_Suche.text())
        (categories, products) = self.shoppingBackend.search_from_text(searchstr)
        self.updateProductsAndCategories(categories, products, "Suchergebnisse")

        if not preview:
            self.leaveSearch(keepResultsVisible=True)

    def leaveSearch(self, keepResultsVisible=False):
        self.lineEdit_Suche.clear()
        if self.stackedWidget.currentIndex() != 0:
            # after search set view from keyboard to basket
            self.stackedWidget.setCurrentIndex(0)
            if not keepResultsVisible:
                self.updateProductsAndCategories()
        # Give focus to lineEdit
        self.lineEdit.setFocus()

    def _check_idle(self):
        """checks whether the GUI is idle for a great time span

        Uses the information from screensaver to check whether the GUI is idle for a hardcoded time span.
        If the GUI is considered idle, then true is returned.
        :rtype: bool
        :return: true if GUI is idle
        """
        idle_state = self.idleTracker.check_idle()
        # check_idle() returns a tupel (state_change, suggested_time_till_next_check, idle_time)
        # the state "idle" is entered after the time configured in self.CATEGORY_VIEW_RESET_TIME
        idle_keyword = "idle"
        if idle_state[0] == idle_keyword:
            return True
        elif idle_state[0] is None and self.idleTracker.last_state == idle_keyword:
            return True
        else:
            return False

    def _reset_if_idle(self):
        """resets the category-view of the GUI if it is idle for a certain timespan

        The function uses self._check_idle() to check whether the screensaver thinks the GUI is idle.
        The timespan for considering the system idle is set in the config-file.

        This function might be called anytime. This means it could even execute during payup-dialogs and similar things.
        Therefore the current order must not be modified or updated by this method to prevent undefined interference
        with other processes.
        """
        if self._check_idle():
            logging.debug("idle timespan passed; execute GUI reset")
            self.on_start_clicked()

    def _clear_cart(self, hide_dialog=False):
        """clear the current cart

        :param show_dialog: whether the user should be asked
        :type show_dialog: bool
        """

        def ask_user():
            """ask the user whether he really wants to clear the cart, return True if he does."""
            reply = QtWidgets.QMessageBox.question(
                self,
                "Message",
                "Willst du den Warenkorb wirklich löschen?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            return reply == QtWidgets.QMessageBox.Yes

        user_answer = True
        if hide_dialog is False:
            user_answer = ask_user()
        if user_answer:
            self.shoppingBackend.delete_current_order()
            self.updateOrder()


def main():
    if "--debug" in sys.argv:
        logging.warn("FIXME: Here you could add a command to attach a debugger")
    # catch SIGINT
    scriptHelper.setupSigInt()
    # setup logging
    scriptHelper.setupLogging("gui.log")

    # set up an application first (to be called before setupGraphicalExceptHook in order to have application for except hook)
    app = QtWidgets.QApplication(sys.argv)

    # error message on exceptions
    scriptHelper.setupGraphicalExceptHook()

    # Hide mouse cursor if configured
    if cfg.getboolean("general", "hide_cursor"):
        app.setOverrideCursor(QtGui.QCursor(QtCore.Qt.BlankCursor))

    # load locale for buttons, thanks to https://stackoverflow.com/questions/9128966/pyqt4-qfiledialog-and-qfontdialog-localization
    translator = QtCore.QTranslator()
    current_locale = QtCore.QLocale.system().name()
    translator.load(
        "qt_%s" % current_locale,
        QtCore.QLibraryInfo.location(QtCore.QLibraryInfo.TranslationsPath),
    )
    app.installTranslator(translator)

    # Set style to KDE Breeze
    app.setStyle("breeze")
    font = QtGui.QFont("Roboto")
    app.setFont(font)

    # style: Roboto (light), minimum size for message boxes
    # for source of QMessageBox see https://codebrowser.dev/qt5/qtbase/src/widgets/dialogs/qmessagebox.cpp.html
    app.setStyleSheet(
        """
        QWidget {font-family: "Roboto";}
        QMessageBox { border: 1px solid #9d9d9d;}
        QMessageBox QLabel, QMessageBox QPushButton { font-size:15pt; }
        QMessageBox QLabel#qt_msgbox_label { min-height: 300px; }
        QMessageBox QDialogButtonBox { min-width: 700px; }
        QMessageBox QPushButton { margin:16px; min-width:150px; min-height:3em; }
        """
    )
    QtGui.QIcon.setThemeName("breeze")
    logging.debug(f"icon theme: {QtGui.QIcon.themeName()}")
    logging.debug(f"icon paths: {[str(x) for x in QtGui.QIcon.themeSearchPaths()]}")

    kt = Kassenterminal()
    kt.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
