
function UIModel() {
    this.setpoint = ko.observable();
    this.temp = ko.observable();
    this.temp_as_of = ko.observable();
    this.heater_on = ko.observable();
    this.setpoints = ko.observableArray();
    this.temp_tol = ko.observable();
    this.const = null;

    this.chart_end = ko.observable();
    this.history = [];
    
    var model = this;
    this.setpointChanged = function(obj, event) {
        if (event.originalEvent) { // user changed
            CONN.send(JSON.stringify({action: 'setpoint', value: model.setpoint()}));
        } else { // program changed
            // ignore
        }
    }

    this.now = ko.observable(new Date());
    setInterval(function() {
	    model.now(new Date());
    }, 1000);
    this.temp_age = ko.computed(function() {
        model.now();
        var ts = moment(model.temp_as_of());
        return ts.fromNow();
    });
    this.temp_stale = ko.computed(function() {
        return model.temp_as_of() == null || (model.now() - new Date(model.temp_as_of())) / 1000. > model.const.staleness;
    });
    
    this.update = function(data) {
        if (data.constants) {
            this.const = data.constants;
            this.const.setpoints.unshift(this.const.setpoint_off);
            this.const.setpoints.push(this.const.setpoint_max);
            this.setpoints(this.const.setpoints);
            this.temp_tol(this.const.tolerance);
        }

        this.setpoint(data.setpoint);
        this.temp(data.cur_temp);
        this.heater_on(data.heater_on);
        this.temp_as_of(data.temp_as_of);

        if (data.history) {
            _.each(data.history, function(e) {
                model.add_historical_point(e);
            });
            var last_pt = this.last_temp();
            var loaded_at = new Date(this.const.server_now);
            if (last_pt != null && !this.data_gap(loaded_at, last_pt.x)) {
                this.chart_end(last_pt.x);
            } else {
                this.chart_end(loaded_at);
            }
        } else {
            var new_point = this.add_historical_point([this.temp_as_of(), this.temp()]);
            if (new_point) {
                this.chart_end(new Date(this.temp_as_of()));
            }
        }
    }
    
    this.add_historical_point = function(e) {
        var cur = {x: new Date(e[0]), y: e[1]};
        var prev = this.last_temp();
        if (prev != null) {
            if (cur.x - prev.x == 0) {
                // same point; do nothing and exit
                return false;
            }
            if ((cur.x - prev.x) / 1000. > this.const.staleness) {
                this.history.push(undefined);
            }
        }
        this.history.push(cur);
        return true;
    }

    this.cull_history_for_window = function(start) {
        while (this.history.length > 0) {
            var first = this.history[0];
            if (first !== undefined && first.x > start) {
                break;
            }
            this.history.shift();
        }
    }

    this.update_chart = function(end) {
        var start = new Date(end - this.const.hist_window * 1000);
        this.cull_history_for_window(start);
        
        var chart = new Chartist.Line('.ct-chart', {series: [{data: this.history}]}, {
            showPoint: false,
            showLine: true,
            axisX: {
                type: Chartist.FixedScaleAxis,
                // convert high/low from date back to int
                high: end-0,
                low: start-0,
                divisor: 6,
                labelInterpolationFnc: function(value) {
                    return moment(value).format('HH:mm');
                },
            },
        });
    }

    // re-render chart when window 'end' changes
    this.chart_end.subscribe(function() {
        model.update_chart(model.chart_end());
    });
    this.now.subscribe(function() {
        if (model.data_gap(model.now(), model.chart_end())) {
            model.chart_end(model.now());
        }
    });

    this.data_gap = function(now, last) {
        return now - last > 2.5 * this.const.polling * 1000;        
    }
    this.last_temp = function() {
        return this.history.length > 0 ? this.history.slice(-1)[0] : null;
    }
    
}

function format_setpoint(n) {
    if (n == 0) {
        return 'OFF';
    } else if (n == 99) {
        return 'MAX';
    } else {
        return n.toFixed(1) + ' \xb0C';
    }
}


function connect(model, mode) {
    var secure = window.location.protocol.startsWith('https');
    if (secure && window.location.host.startsWith('localhost')) {
	    alert("chrome doesn't support secure websockets to 'localhost'; use an actual IP address");
    }
    
    var conn = new WebSocket((secure ? 'wss' : 'ws') + '://' + window.location.host + '/socket/');
    $('#connectionstatus').text('connecting to server...');
    
    var connectionLost = function() {
	    $('#connectionstatus').text('connection to server lost; reload to reconnect');
	    $('#ui').css('opacity', .65);
	    $('#connectionstatus').css('font-weight', 'bold');
	    scrollTo(0, 0);
    }
    
    conn.onopen = function () {
	    $('#connectionstatus').text('');
    };
    conn.onclose = function() {
	    connectionLost();
    };
    conn.onerror = function (error) {
        console.log('websocket error ' + error);
	    connectionLost();
    };
    conn.onmessage = function (e) {
	    console.log('receiving msg');
        var data = JSON.parse(e.data);
	    console.log(data);
        model.update(data);
    };
    CONN = conn;
}

function init() {
    _init('main');
}

function _init(mode) {
    var model = new UIModel();
    ko.applyBindings(model);
    connect(model, mode);
    MODEL = model;
}

